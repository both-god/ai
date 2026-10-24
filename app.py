import ast
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import operator
import os
import re
import sqlite3
from urllib.parse import quote_plus
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template, request
import requests

app = Flask(__name__)

# សំខាន់បំផុតសម្រាប់ Vercel៖ ត្រូវដាក់ Database ក្នុង /tmp ទើបមិនចេញ Error 500
DB_FILE = '/tmp/spx_data_server.db'
executor = ThreadPoolExecutor(max_workers=4)


# ==========================================
# ១. ប្រព័ន្ធ DATA SERVER (SQLite លើ /tmp)
# ==========================================
def init_data_server():
  try:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
            CREATE TABLE IF NOT EXISTS spx_memory (
                query_hash TEXT PRIMARY KEY,
                query_text TEXT,
                category TEXT,
                learned_code TEXT,
                answer_text TEXT,
                sources_json TEXT,
                hits INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    conn.commit()
    conn.close()
  except Exception as e:
    print(f'DB Init Warning: {e}')


init_data_server()


def get_from_data_server(query):
  try:
    init_data_server()
    q_hash = hashlib.md5(query.lower().strip().encode('utf-8')).hexdigest()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        'SELECT answer_text, learned_code, sources_json, hits FROM spx_memory'
        ' WHERE query_hash = ?',
        (q_hash,),
    )
    row = c.fetchone()
    if row:
      c.execute(
          'UPDATE spx_memory SET hits = hits + 1 WHERE query_hash = ?',
          (q_hash,),
      )
      conn.commit()
      conn.close()
      return {
          'answer': row[0],
          'code': row[1],
          'sources': json.loads(row[2]),
          'cached': True,
          'hits': row[3] + 1,
      }
    conn.close()
  except Exception as e:
    print(f'DB Read Warning: {e}')
  return None


def save_to_data_server(query, category, code_snippet, answer, sources):
  try:
    init_data_server()
    q_hash = hashlib.md5(query.lower().strip().encode('utf-8')).hexdigest()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        """
            INSERT OR REPLACE INTO spx_memory 
            (query_hash, query_text, category, learned_code, answer_text, sources_json)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            q_hash,
            query,
            category,
            code_snippet,
            answer,
            json.dumps(sources, ensure_ascii=False),
        ),
    )
    conn.commit()
    conn.close()
  except Exception as e:
    print(f'DB Save Warning: {e}')


def get_server_stats():
  try:
    init_data_server()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT COUNT(*), COALESCE(SUM(hits), 0) FROM spx_memory')
    total_learned, total_hits = c.fetchone()
    conn.close()
    return {'learned_items': total_learned, 'total_hits': total_hits}
  except Exception:
    return {'learned_items': 0, 'total_hits': 0}


# ==========================================
# ២. មុខងារគណនាគណិតវិទ្យា (Math Engine)
# ==========================================
ALLOWED_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
}


def eval_math_expr(node):
  if isinstance(node, ast.Constant):
    return node.value
  elif isinstance(node, ast.BinOp):
    return ALLOWED_OPERATORS[type(node.op)](
        eval_math_expr(node.left), eval_math_expr(node.right)
    )
  elif isinstance(node, ast.UnaryOp):
    return ALLOWED_OPERATORS[type(node.op)](eval_math_expr(node.operand))
  raise TypeError(node)


def try_calculate(text):
  math_matches = re.findall(r'[\d\.\s\+\-\*\/\(\)\%\^]+', text)
  for match in math_matches:
    clean_expr = match.strip().replace('^', '**')
    if any(op in clean_expr for op in ['+', '-', '*', '/', '%']) and any(
        c.isdigit() for c in clean_expr
    ):
      try:
        tree = ast.parse(clean_expr, mode='eval')
        return f'{clean_expr} = {eval_math_expr(tree.body)}'
      except Exception:
        continue
  return None


# ==========================================
# ៣. ប្រព័ន្ធស្វែងយល់កូដ និងបង្កើត Base Code
# ==========================================
CODE_KEYWORDS = [
    'code',
    'កូដ',
    'html',
    'python',
    'css',
    'js',
    'javascript',
    'flask',
    'api',
    'website',
    'បង្កើត',
    'សរសេរ',
    'script',
    'bot',
    'function',
    'server',
]


def is_code_request(query):
  q_lower = query.lower()
  return any(kw in q_lower for kw in CODE_KEYWORDS)


def scrape_code_from_url(url):
  try:
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    resp = requests.get(url, headers=headers, timeout=3)
    if resp.status_code == 200:
      soup = BeautifulSoup(resp.text, 'html.parser')
      code_blocks = []
      for tag in soup.find_all(['pre', 'code']):
        text = tag.get_text().strip()
        if len(text) > 45 and ('\n' in text or ';' in text or 'def ' in text):
          code_blocks.append(text[:1500])
          if len(code_blocks) >= 2:
            break
      return '\n\n'.join(code_blocks)
  except Exception:
    pass
  return ''


def build_base_code_template(query):
  q = query.lower()
  if 'html' in q and ('python' in q or 'server' in q or 'flask' in q):
    return """<!-- [1] Frontend: index.html (បញ្ជូនទិន្នន័យទៅ Python Server) -->
<!DOCTYPE html>
<html lang="km">
<head>
    <meta charset="UTF-8">
    <title>spX App Base</title>
</head>
<body>
    <h2>spX 4.0 Base Interface</h2>
    <input id="dataInput" placeholder="បញ្ចូលទិន្នន័យ...">
    <button onclick="sendToPython()">ផ្ញើទៅ Python Server</button>
    <pre id="output"></pre>

    <script>
        async function sendToPython() {
            const val = document.getElementById('dataInput').value;
            const res = await fetch('/api/process', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ input: val })
            });
            const data = await res.json();
            document.getElementById('output').innerText = data.result;
        }
    </script>
</body>
</html>"""
  elif 'html' in q or 'website' in q:
    return """<!DOCTYPE html>
<html lang="km">
<head>
    <meta charset="UTF-8">
    <title>spX 4.0 Generated Page</title>
    <style>
        body { background: #0d1117; color: #58a6ff; font-family: sans-serif; padding: 2rem; text-align: center; }
        .card { background: #161b22; border: 1px solid #30363d; padding: 24px; border-radius: 12px; max-width: 500px; margin: auto; }
        button { background: #238636; color: white; border: none; padding: 10px 20px; border-radius: 6px; cursor: pointer; }
    </style>
</head>
<body>
    <div class="card">
        <h1>spX ai model 4.0 Base UI</h1>
        <p>បង្កើតឡើងដោយស្វ័យប្រវត្តិពីប្រព័ន្ធ Data Server</p>
        <button onclick="alert('spX 4.0 Active!')">សាកល្បងចុច</button>
    </div>
</body>
</html>"""
  else:
    return """# spX ai model 4.0 - Python Base Script
def spx_main():
    print("spX ai model 4.0 Base Code Ready!")

if __name__ == "__main__":
    spx_main()"""


# ==========================================
# ៤. មុខងារ Search លឿនដោយមិនប្រើ Library ធ្ងន់លើ Vercel
# ==========================================
def fast_search_browser(query, code_mode=False):
  results = []
  search_query = f'{query} code example' if code_mode else query
  try:
    url = f'https://html.duckduckgo.com/html/?q={quote_plus(search_query)}'
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )
    }
    resp = requests.get(url, headers=headers, timeout=5)
    if resp.status_code == 200:
      soup = BeautifulSoup(resp.text, 'html.parser')
      for item in soup.select('.result')[:4]:
        title_el = item.select_one('.result__title a')
        snippet_el = item.select_one('.result__snippet')
        if title_el and snippet_el:
          link = title_el.get('href', '')
          results.append({
              'title': title_el.get_text(strip=True),
              'snippet': snippet_el.get_text(strip=True),
              'link': link,
          })
  except Exception as e:
    print(f'Search Warning: {e}')

  learned_code = ''
  if code_mode and results:
    urls = [r['link'] for r in results[:2] if r['link'].startswith('http')]
    scraped_list = list(executor.map(scrape_code_from_url, urls))
    for code_item in scraped_list:
      if code_item:
        learned_code = code_item
        break

  if code_mode and not learned_code:
    learned_code = build_base_code_template(query)

  return results, learned_code


# ==========================================
# ៥. រៀបចំចម្លើយរបស់ spX ai model 4.0
# ==========================================
def synthesize_spx_answer(query, search_results, math_result, learned_code):
  groq_api_key = os.environ.get('GROQ_API_KEY')

  if groq_api_key:
    context = '\n'.join(
        [f"- {r['title']}: {r['snippet']}" for r in search_results]
    )
    if learned_code:
      context += f'\n\nLearned Code Base:\n{learned_code}'
    prompt = f"""អ្នកគឺជា spX ai model 4.0 ដែលមានប្រព័ន្ធ Data Server ផ្ទាល់ខ្លួន។
ចូរឆ្លើយសំណួរ ឬសរសេរកូដឱ្យបានត្រឹមត្រូវតាមការស្នើសុំ (ប្រើ Tag ដូចជា [spx_core] [spx_code] [spx_data] [spx_check] ជំនួស Emoji)៖

ទិន្នន័យពីការ Search និង Data Server:
{context}

សំណួរ: {query}"""
    try:
      res = requests.post(
          'https://api.groq.com/openai/v1/chat/completions',
          headers={
              'Authorization': f'Bearer {groq_api_key}',
              'Content-Type': 'application/json',
          },
          json={
              'model': 'llama-3.3-70b-versatile',
              'messages': [{'role': 'user', 'content': prompt}],
              'temperature': 0.25,
          },
          timeout=8,
      )
      if res.status_code == 200:
        return res.json()['choices'][0]['message']['content']
    except Exception:
      pass

  parts = []
  if math_result:
    parts.append(f'[spx_calc] **លទ្ធផលគណនាដោយប្រព័ន្ធ spX 4.0៖**\n`{math_result}`')

  if learned_code:
    parts.append(
        '[spx_data] **spX ai model 4.0 បានស្វែងយល់ និងរក្សាទុកទម្រង់កូដចូលក្នុង'
        ' Data Server រួចរាល់!**'
    )
    parts.append(
        f'[spx_code] **កូដដែលបានបង្កើត និងរៀបចំចេញពី Base Server៖**\n```html\n{learned_code}\n```'
    )

  if search_results:
    parts.append('[spx_search] **ព័ត៌មានបន្ថែមដែលបានវិភាគពី Browser៖**')
    for idx, item in enumerate(search_results, 1):
      parts.append(f"**[{idx}] {item['title']}**\n{item['snippet']}")

  if not parts:
    parts.append(
        '[spx_error] មិនអាចទាញយកទិន្នន័យបានទេ សូមសាកល្បងពាក្យគន្លឹះផ្សេង។'
    )

  return '\n\n'.join(parts)


@app.route('/')
def index():
  stats = get_server_stats()
  return render_template('index.html', stats=stats)


@app.route('/api/ask', methods=['POST'])
def ask_spx():
  data = request.json or {}
  query = data.get('query', '').strip()
  if not query:
    return jsonify({'error': 'សូមបញ្ចូលសំណួរ ឬកូដដែលចង់បង្កើត!'}), 400

  cached_data = get_from_data_server(query)
  if cached_data:
    stats = get_server_stats()
    return jsonify({
        'answer': (
            f"[spx_bolt] **ទាញចេញពី spX Data Server ផ្ទាល់ (Hit"
            f" #{cached_data['hits']})**\n\n"
            + cached_data['answer']
        ),
        'sources': cached_data['sources'],
        'cached': True,
        'stats': stats,
    })

  math_result = try_calculate(query)
  code_mode = is_code_request(query)
  search_results, learned_code = fast_search_browser(query, code_mode=code_mode)

  answer = synthesize_spx_answer(
      query, search_results, math_result, learned_code
  )
  category = 'code' if code_mode else ('math' if math_result else 'general')
  save_to_data_server(query, category, learned_code, answer, search_results)

  stats = get_server_stats()
  return jsonify({
      'answer': answer,
      'sources': search_results,
      'cached': False,
      'stats': stats,
  })


if __name__ == '__main__':
  port = int(os.environ.get('PORT', 5000))
  app.run(host='0.0.0.0', port=port)
