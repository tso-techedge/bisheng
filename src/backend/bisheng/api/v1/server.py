import asyncio
import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import List

import requests
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import delete
from sqlmodel import select

from bisheng.api.services.user_service import get_login_user
from bisheng.api.v1.schemas import UnifiedResponseModel, resp_200
from bisheng.database.base import session_getter
from bisheng.database.models.model_deploy import (ModelDeploy, ModelDeployDao, ModelDeployInfo,
                                                  ModelDeployQuery, ModelDeployRead,
                                                  ModelDeployUpdate)
from bisheng.database.models.server import Server, ServerCreate, ServerRead
from bisheng.database.models.sft_model import SftModelDao
from bisheng.utils.logger import logger

# build router
router = APIRouter(prefix='/server', tags=['server'], dependencies=[Depends(get_login_user)])

thread_pool = ThreadPoolExecutor(3)
required_param = ['type', 'pymodel_type', 'gpu_memory', 'instance_groups']


@router.post('/add')
async def add_server(*, server: ServerCreate):
    try:
        db_server = Server.from_orm(server)
        with session_getter() as session:
            session.add(db_server)
            session.commit()
            session.refresh(db_server)
        # 拉取模型
        # await update_model(db_server.endpoint, db_server.server)
        return resp_200(db_server)
    except Exception as exc:
        logger.error(f'Error add server: {exc}')
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get('/list_server')
async def list_server():
    try:
        with session_getter() as session:
            rt_server = session.exec(select(Server)).all()
        if rt_server:
            return resp_200(rt_server)
        else:
            return resp_200([])
    except Exception as exc:
        logger.error(f'Error delete server: {exc}')
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete('/{server_id}', status_code=200)
async def delete_server(*, server_id: int):
    try:
        with session_getter() as session:
            rt_server = session.get(Server, server_id)
            if rt_server:
                session.delete(rt_server)
                # 删除服务带带模型
                session.exec(delete(ModelDeploy).where(ModelDeploy.server == str(server_id)))
                session.commit()

        return resp_200()
    except Exception as exc:
        logger.error(f'Error delete server: {exc}')
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get('/list')
async def list(*, query: ModelDeployQuery = None):
    try:
        # 更新模型
        with session_getter() as session:
            servers = session.exec(select(Server)).all()
        id2server = {server.id: server for server in servers}
        name2server = {server.server: server for server in servers}
        all_sft_model = SftModelDao.get_all_sft_model()
        sft_model_dict = {one.model_name: True for one in all_sft_model}
        for server in servers:
            await update_model(server.endpoint, server.id)
        sql = select(ModelDeploy)
        if query and query.server:
            sql = sql.where(ModelDeploy.server == str(name2server.get(query.server).id))
        with session_getter() as session:
            db_model = session.exec(sql.order_by(ModelDeploy.model)).all()
        res = []
        for model in db_model:
            # 说明是在删除rt服务后，发布成功的模型，所以会写入到model deploy数据内，删除此遗留数据
            model_server = id2server.get(int(model.server))
            if not model_server:
                ModelDeployDao.delete_model(model)
                continue
            model.server = model_server.server
            res.append(ModelDeployInfo(**model.dict(), sft_support=sft_model_dict.get(model.model, False)))
        return resp_200(data=res)
    except Exception as exc:
        logger.error(f'Error add server: {exc}', exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get('/model/{deploy_id}')
async def get_model_deploy(*, deploy_id: int):
    try:
        model_deploy = ModelDeployDao.find_model(deploy_id)
        if not ModelDeployDao:
            raise HTTPException(status_code=404, detail='配置不存在')
        return resp_200(data=model_deploy)
    except Exception as exc:
        logger.error(f'Error get model deploy: {exc}')
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post('/update')
async def update_deploy(*, deploy: ModelDeployUpdate):
    try:
        with session_getter() as session:
            db_deploy = session.get(ModelDeploy, deploy.id)
        if not db_deploy:
            raise HTTPException(status_code=404, detail='配置不存在')

        deploy_data = deploy.model_dump(exclude_unset=True)
        for key, value in deploy_data.items():
            setattr(db_deploy, key, value)
        with session_getter() as session:
            session.add(db_deploy)
            session.commit()
            session.refresh(db_deploy)
        return resp_200(db_deploy)
    except Exception as exc:
        logger.error(f'Error add server: {exc}')
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post('/load', status_code=201)
async def load(*, deploy_id: dict):
    with session_getter() as session:
        db_deploy = session.get(ModelDeploy, deploy_id.get('deploy_id'))
    if not db_deploy:
        raise HTTPException(status_code=404, detail='配置不存在')
    try:
        endpoint = db_deploy.endpoint.replace('http://', '').split('/')[0]
        url = f'http://{endpoint}/v2/repository/models/{db_deploy.model}/load'
        data = db_deploy.config
        # #validator config
        config = json.loads(data)
        for key in required_param:
            if key not in config.get('parameters').keys() or not config.get('parameters')[key]:
                # 不OK
                raise Exception(f'必传参数{key}未传')
        # 先设置为上线中
        logger.info(f'load_model=success url={url} config={data}')
        db_deploy.status = '上线中'
        with session_getter() as session:
            session.add(db_deploy)
            session.commit()
            session.refresh(db_deploy)
        # 真正开始执行load
        asyncio.get_event_loop().run_in_executor(thread_pool, load_model, url, data,
                                                 deploy_id.get('deploy_id'))
        return resp_200()
    except Exception as exc:
        logger.error(f'Error load model: {exc}')
        db_deploy.status = '异常'
        db_deploy.remark = error_translate(str(exc))
        with session_getter() as session:
            session.add(db_deploy)
            session.commit()
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post('/unload', status_code=201)
async def unload(*, deploy_id: dict):
    try:
        # 缓存本地
        with session_getter() as session:
            db_deploy = session.get(ModelDeploy, deploy_id.get('deploy_id'))
        if not db_deploy:
            raise HTTPException(status_code=404, detail='配置不存在')
        endpoint = db_deploy.endpoint.replace('http://', '').split('/')[0]
        url = f'http://{endpoint}/v2/repository/models/{db_deploy.model}/unload'
        resp = requests.post(url)
        logger.info(f'unload_model=success url={url} code={resp.status_code}')
        # 更新状态
        db_deploy.status = '下线中'
        with session_getter() as session:
            session.add(db_deploy)
            session.commit()
            session.refresh(db_deploy)
        return resp_200()

    except Exception as exc:
        logger.error(f'Error add server: {exc}')
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get('/GPU', status_code=200)
async def get_gpu():
    try:
        # 缓存本地
        with session_getter() as session:
            db_service = session.exec(select(Server)).all()
        if not db_service:
            raise HTTPException(status_code=404, detail='配置不存在')

        resp = []
        for service in db_service:
            ip = service.endpoint.split(':')[0]
            port = int(service.endpoint.split(':')[1]) + 1
            url = f'http://{ip}:{port}/metrics'
            gpu = await queryGPU(url)
            if gpu:
                for g in gpu:
                    g.update({'server': service.server})
                resp.append(gpu)
            else:
                logger.error(f'gpu_query_none url={url}')
        return resp_200({'list': resp})

    except Exception as exc:
        logger.error(f'Error add server: {exc}')
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def load_model(url: str, data: str, deploy_id: int):
    response = requests.post(url, data=data)
    if response.status_code == 200:
        logger.info(f'load_model={url} result=success')
    else:
        with session_getter() as session:
            logger.error(f'load_model=fail code={response.status_code}, return={response.text}')
            db_deploy = session.get(ModelDeploy, deploy_id)
            db_deploy.status = '异常'
            reason = json.loads(response.text).get('error')
            db_deploy.remark = error_translate(reason)
            session.add(db_deploy)
            session.commit()
            session.refresh(db_deploy)


pattern = r'gpu_uuid="([^"]+)"'


async def queryGPU(query_url: str):
    resp = requests.get(query_url)
    if resp.status_code != 200:
        return []
    content = resp.text
    lines = content.split('\n')
    gpus = []
    utility = {}
    device_dict = {}
    total_mem = {}
    used_mem = {}
    for line in lines:
        if '#' in line:
            continue

        if 'nv_gpu_utilization' in line:
            # nv_gpu_utilization{gpu_uuid="GPU-c8a73d12-b320-0910-68f1-a74bd0d626bd"}
            match = re.search(pattern, line)
            gpu_uuid = match.group(1) if match else None
            utility[gpu_uuid] = round(float(line.split(' ')[1]), 2)

        if 'nv_gpu_uuid_to_deviceid' in line:
            match = re.search(pattern, line)
            gpu_uuid = match.group(1) if match else None
            device_dict[gpu_uuid] = line.split(' ')[1]

        if 'nv_gpu_memory_total_bytes' in line:
            match = re.search(pattern, line)
            gpu_uuid = match.group(1) if match else None
            total_mem[gpu_uuid] = int(line.split(' ')[1].strip()) / 1024 / 1024 / 1024

        if 'nv_gpu_memory_used_bytes' in line:
            match = re.search(pattern, line)
            gpu_uuid = match.group(1) if match else None
            used_mem[gpu_uuid] = int(line.split(' ')[1].strip()) / 1024 / 1024 / 1024
    # 整理最终对象
    for uuid, deviceid in device_dict.items():
        gpu_res = {}
        gpu_res['gpu_id'] = deviceid
        gpu_res['gpu_uuid'] = uuid
        gpu_res['gpu_total_mem'] = '%.2f G' % (total_mem[uuid])
        gpu_res['gpu_used_mem'] = '%.2f G' % (total_mem[uuid] - used_mem[uuid])
        gpu_res['gpu_utility'] = utility[uuid]
        gpus.append(gpu_res)
    gpus = sorted(gpus, key=lambda x: x['gpu_id'])
    return gpus


async def update_model(endpoint: str, server_id: int):
    try:
        url = f'http://{endpoint}/v2/repository/index'
        resp = requests.post(url)
        if resp.status_code != 200:
            return []
        content = resp.text
        models = json.loads(content)
    except Exception as e:
        logger.error(f'{str(e)}')
        return []
    with session_getter() as session:
        db_deploy = session.exec(
            select(ModelDeploy).where(ModelDeploy.server == str(server_id))).all()
        model_dict = {deploy.model: deploy for deploy in db_deploy}
        model_delete = {model.id for key, model in model_dict.items()}
        for model in models:
            model_name = model['name']
            status = model.get('state')
            reason = model.get('reason')
            if model_name in model_dict:
                db_model = model_dict.get(model_name)
                # 依然存在
                model_delete.remove(db_model.id)
            else:
                db_model = ModelDeploy(server=str(server_id),
                                       endpoint=f'http://{endpoint}/v2.1/models',
                                       model=model_name)
            # 当前是上下线中，需要判断
            if status == 'READY':
                db_model.status = '已上线'
            if status == 'UNAVAILABLE':
                if reason == 'unloaded':
                    db_model.status = '未上线'
                elif reason != 'unloaded':
                    db_model.status = '异常'
                    db_model.remark = error_translate(reason)
            if not db_model.status or not status:
                db_model.status = '未上线'

            if not db_model.config:
                # 初始化config
                config_url = f'http://{endpoint}/v2/repository/models/{model_name}/config'
                resp = requests.post(config_url)
                db_model.config = resp.text
            session.add(db_model)
        if model_delete:
            session.exec(delete(ModelDeploy).where(ModelDeploy.id.in_(model_delete)))
        session.commit()


def error_translate(err: str):
    if 'OutOfMemoryError' in err:
        reason = f"上线失败，显卡{err.split('(')[1].split(';')[0]}显存不足"
    else:
        reason = f'上线失败，{err}'

    return reason[:512]
