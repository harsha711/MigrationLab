#!/usr/bin/env bash
# Runs on the runner machine, after the SSH tunnel to the Brev instance is up.
#
# Core validation from docs/serving.md before any controller run:
#   1. /v1/models reports the served Lightning name.
#   2. One chat completion, sent with the same fields as migproto/agents/llm.py
#      (sampling, chat_template_kwargs, json_object response format), returns non-empty
#      message.content and usage metadata.
# Exits non-zero if either check fails.
set -euo pipefail

BASE=${MIGPROTO_LLM_BASE_URL:-http://localhost:8000/v1}
MODEL=${MIGPROTO_LLM_MODEL:-nemotron-3.5-lightning}
KEY=${MIGPROTO_LLM_API_KEY:-unused}

echo "== GET $BASE/models"
models=$(curl -fsS --max-time 15 "$BASE/models" -H "Authorization: Bearer $KEY")
echo "$models"
echo "$models" | python3 -c "
import json, sys
ids = [m['id'] for m in json.load(sys.stdin)['data']]
sys.exit(0 if '$MODEL' in ids else f'served model {\"$MODEL\"} not in {ids}')"

echo "== POST $BASE/chat/completions"
reply=$(curl -fsS --max-time 300 "$BASE/chat/completions" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d @- <<EOF
{"model": "$MODEL",
 "messages": [{"role": "user", "content": "Reply with a JSON object {\"ok\": true, \"answer\": <2+3>}."}],
 "temperature": ${MIGPROTO_LLM_TEMPERATURE:-1.0}, "top_p": ${MIGPROTO_LLM_TOP_P:-0.95},
 "chat_template_kwargs": {"force_nonempty_content": true, "enable_thinking": true},
 "response_format": {"type": "json_object"}}
EOF
)
echo "$reply" | python3 -c "
import json, sys
body = json.load(sys.stdin)
msg = body['choices'][0]['message']
content = msg.get('content') or ''
reasoning = msg.get('reasoning_content') or msg.get('reasoning') or ''
print('content:', content[:300])
print('reasoning chars:', len(reasoning))
print('usage:', body.get('usage'))
problems = []
if not content.strip(): problems.append('empty message.content')
if not body.get('usage'): problems.append('no usage metadata')
try: json.loads(content)
except ValueError: problems.append('content is not JSON')
sys.exit('FAIL: ' + '; '.join(problems) if problems else 0)"
echo "OK: endpoint ready for agent-loop --proposals llm"
