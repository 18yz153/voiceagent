# guard_agent.py
import os
import re
import aiosqlite
from fastapi import APIRouter
from pydantic import BaseModel
from openai import AsyncOpenAI
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 初始化独立的 Router
router = APIRouter()

# 初始化大模型客户端
aclient = AsyncOpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"), # 记得在 .env 文件里换成你的 DeepSeek Key
    base_url="https://api.deepseek.com"    # 强制让请求发往 DeepSeek 服务器
)
DB_FILE = "visitors.db"

# 定义前端请求的数据模型
class GuardQuery(BaseModel):
    question: str

# 提取 Schema 常量
SCHEMA_PROMPT = """
表名: visitor_history
字段:
- phone_number (TEXT, 主键, 访客手机号)
- plate_number (TEXT, 车牌号)
- company_name (TEXT, 拜访公司)
- purpose (TEXT, 来访目的)
- last_visit_time (TIMESTAMP, 最后来访时间)
"""

# 注意这里使用的是 @router.post 而不是 @app.post
@router.post("/guard/ask")
async def guard_query_api(query: GuardQuery):
    """
    纯数据版 NL2SQL 接口：接收自然语言，返回 SQLite 原生查询结果
    """
    question = query.question
    
    # 1. 异步 NL2SQL
    sql_prompt = f"""
    你是一个精准的 SQLite 数据库查询助手。根据以下表结构，将用户问题转化为 SQL 语句。
    {SCHEMA_PROMPT}
    
    用户问题: "{question}"
    约束：只返回 SQL 语句本身，绝对不要任何 markdown 格式（如 ```sql），不要任何解释。
    """
    
    try:
        response = await aclient.chat.completions.create(
            model="deepseek-v4-flash", 
            messages=[{"role": "user", "content": sql_prompt}],
            temperature=0
        )
        raw_sql = response.choices[0].message.content.strip()
        clean_sql = re.sub(r"```sql\n?|```", "", raw_sql).strip()
        print(f"🔎 门卫查询 Agent 生成 SQL: {clean_sql}")
    except Exception as e:
        return {"status": "error", "message": f"LLM 生成 SQL 失败: {str(e)}"}

    # 2. 异步安全查库
    try:
        uri = f"file:{DB_FILE}?mode=ro"
        async with aiosqlite.connect(uri, uri=True) as db:
            async with db.execute(clean_sql) as cursor:
                raw_data = await cursor.fetchall()
                print(f"📊 数据库查询结果: {raw_data}")
                
                return {
                    "status": "success",
                    "question": question,
                    "sql_executed": clean_sql,
                    "data": raw_data
                }
    except Exception as e:
        return {"status": "error", "message": f"SQL 执行异常: {str(e)}", "sql_executed": clean_sql}