import os, json, re
from typing import Dict, Any
from openai import OpenAI

_PROVIDER = os.getenv('LLM_PROVIDER','openai')
_OPENAI_MODEL = os.getenv('OPENAI_MODEL','gpt-4o-mini')

_client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

def _chat(messages, temperature=0.1):
    return _client.chat.completions.create(
        model=_OPENAI_MODEL,
        messages=messages,
        temperature=temperature
    ).choices[0].message.content.strip()

def chat_json(system: str, user: str, temperature: float = 0.1) -> Dict[str, Any]:
    txt = _chat([{"role":"system","content":system},{"role":"user","content":user}], temperature)
    m = re.search(r"\{[\s\S]*\}", txt)
    return json.loads(m.group(0)) if m else {"raw": txt}

def chat_text(system: str, user: str, temperature: float = 0.1) -> str:
    return _chat([{"role":"system","content":system},{"role":"user","content":user}], temperature)
