import os, json
from openai import OpenAI

client = OpenAI(api_key=os.environ.get("DEEPSEEK_API_KEY"),
                base_url='https://api.deepseek.com')

if not client.api_key:
    print("FATAL: set DEEPSEEK_API_KEY env var")
    exit(1)

def read_file(path: str) -> str:
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()

tools = [{
    'type': 'function',
    'function': {
        'name': 'read_file',
        'description': '读取文件内容',
        'parameters': {
            'type': 'object',
            'properties': {'path': {'type': 'string'}},
        'required': ['path'],
        },
    },
}]

messages = [{'role': 'user', 'content': "读一下 README.md，告诉我这个项目是做什么的"}]

while True:
    resp = client.chat.completions.create(
        model='deepseek-v4-flash', messages=messages, tools=tools
    )
    msg = resp.choices[0].message
    messages.append(msg)
    if not msg.tool_calls:
        print('最终答案为：', msg.content)
        break
    for call in msg.tool_calls:
        args = json.loads(call.function.arguments)
        result = read_file(**args)
        messages.append({'role': 'tool',
                         'tool_call_id': call.id,
                         'content': result})