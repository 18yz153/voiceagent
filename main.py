import os
from contextlib import asynccontextmanager
import aiosqlite
import httpx
from fastapi import FastAPI, Request, BackgroundTasks
from dotenv import load_dotenv
from query_agent import router as guard_router
# 加载环境变量
load_dotenv()

DB_FILE = "visitors.db"

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI 官方推荐的生命周期管理器
    应用启动时执行初始化（有且仅有一遍），应用关闭时释放资源
    """
    # 异步初始化数据库，创建历史访客表
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS visitor_history (
                phone_number TEXT PRIMARY KEY,
                plate_number TEXT,
                company_name TEXT,
                purpose TEXT,
                last_visit_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()
    print("🚀 数据库异步初始化完成，服务准备就绪。")
    yield
    # 这里可以处理应用关闭时的资源回收（如关闭全局连接池等）
    print("🛑 服务正在关闭...")

# 将生命周期挂载到 App 上
app = FastAPI(lifespan=lifespan)
app.include_router(guard_router)

WECOM_WEBHOOK_URL = os.getenv("WEBHOOK_URL")

async def process_post_call_tasks(phone: str, plate: str, company: str, purpose: str):
    """
    后台任务：负责发微信和写数据库。
    即使这里面全炸了，也完全不影响大模型和访客的通话体验。
    """
    # 异步持久化到数据库
    if phone != "未知号码":
        try:
            async with aiosqlite.connect(DB_FILE) as db:
                await db.execute("""
                    REPLACE INTO visitor_history (phone_number, plate_number, company_name, purpose, last_visit_time)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (phone, plate, company, purpose))
                await db.commit()
            print(f"🎯 访客记录后台持久化成功: {phone}")
        except Exception as e:
            print("后台数据库写入异常:", str(e))

@app.post("/webhook/visitor")
async def handle_visitor_info(request: Request, background_tasks: BackgroundTasks):
    # 1. 接收 Retell 传过来的 JSON 数据
    payload = await request.json()
    print("收到 Retell 请求数据:", payload)

    # 2. 解析数据 (假设你在 Retell 的 Tool 参数定义为 plate_number, company_name, phone_number)
    # Retell 的 Custom Tool 通常会将参数放在 'args' 字段中
    args = payload.get("args", payload) 
    
    plate = args.get("plate_number", "未知车牌")
    company = args.get("company_name", "未知公司")
    phone = args.get("phone_number", "未知号码")
    purpose = args.get("purpose", "未知目的") 
    # 3. 组装要推送到微信的消息格式
    msg_content = (
        f"【园区访客通行请求】\n\n"
        f"车牌号码：{plate}\n"
        f"拜访公司：{company}\n"
        f"联系电话：{phone}\n"
        f"访问目的：{purpose}\n\n"
        f"请门卫确认信息后遥控抬杆放行。"
    )
    wecom_payload = {
        "msgtype": "text",
        "text": {
            "content": msg_content
        }
    }
    wecom_success = False
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(WECOM_WEBHOOK_URL, json=wecom_payload, timeout=5.0)
            print("企业微信异步推送结果:", response.text)
            wecom_success = True
        except Exception as e:
            print("企业微信异步推送异常:", str(e))

    background_tasks.add_task(
        process_post_call_tasks, 
        phone, plate, company, purpose
    )

    if wecom_success:
        return {
            "status": "success",
            "message": "门卫已成功收到通知，请直接告诉访客：门卫师傅马上为您抬杆，请稍等。"
        }
    
    else:
        return {
            "status": "error",
            "message": "企业微信网络异常，未能通知到门卫。请温柔地告诉访客：系统通讯有点异常，麻烦您按一下旁边的对讲门铃，或者下车跟门卫师傅说一声。"
        }

# inbound webhook for Retell's non-blocking callback when the call is just connected
@app.post("/retell/dynamic")
async def handle_retell_dynamic(request: Request):
    """
    Retell 通话刚接通时的非阻塞拦截回调接口
    """
    payload = await request.json()
    from_number = payload.get("from_number", "")
    if from_number == "WEB_TEST":
        print("检测到网页测试拨入，强制注入模拟来电号码")
        from_number = "13080000000"
    dynamic_variables = {
        "is_returning": "false",
        "history_plate": "",
        "history_company": "",
        "history_purpose": ""
    }

    if from_number:
        try:
            # 异步读取 SQLite 进行历史匹配
            async with aiosqlite.connect(DB_FILE) as db:
                async with db.execute(
                    "SELECT plate_number, company_name, purpose FROM visitor_history WHERE phone_number = ?", 
                    (from_number,)
                ) as cursor:
                    row = await cursor.fetchone()

            if row:
                dynamic_variables["is_returning"] = "true"
                dynamic_variables["history_plate"] = row[0]
                dynamic_variables["history_company"] = row[1]
                dynamic_variables["history_purpose"] = row[2]
                print(f"⚡ 异步识别回访客! 号码: {from_number}")
            else:
                print(f"⚡ 异步识别新访客! 号码: {from_number}")
        except Exception as e:
            print("异步读取数据库匹配异常:", str(e))

    print("返回给 Retell 的动态变量:", dynamic_variables)
    return {
        "dynamic_variables": dynamic_variables
    }



if __name__ == "__main__":
    import uvicorn
    # 启动服务，运行在本地 8000 端口
    uvicorn.run(app, host="0.0.0.0", port=8000)