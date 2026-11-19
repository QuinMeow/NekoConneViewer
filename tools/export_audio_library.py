"""Build the offline voice browser from verified export rows."""

import csv
import json
from pathlib import Path


TEMPLATE = '''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>NekoConne | 语音档案</title>
<style>
:root{color-scheme:light;--ink:#1a3633;--green:#00695b;--line:#c5cebb}*{box-sizing:border-box}body{margin:0;color:var(--ink);background:linear-gradient(110deg,#faf5e7,#e8eee3);font:16px/1.5 'Microsoft YaHei',sans-serif}main{max-width:1050px;margin:auto;padding:32px clamp(16px,4vw,48px)}.mast{font:700 12px/1.5 Georgia,serif;letter-spacing:.2em;color:var(--green)}h1{font:normal clamp(34px,5vw,62px)/1.1 Georgia,'Songti SC',serif;margin:6px 0 16px}p{margin:8px 0}.filters{display:flex;gap:10px;flex-wrap:wrap;margin:22px 0 10px}label{font-size:13px;font-weight:600}.search{flex:1 1 210px}input,select,button{font:inherit;color:inherit;padding:8px 10px;border:1px solid var(--line);background:#fffdf6;border-radius:5px}label input,label select{display:block;width:100%;margin-top:4px}button,a{cursor:pointer;color:var(--green)}.play{border:none;background:none;text-align:left;word-break:break-word;padding:4px 0;font-weight:600}.bar{position:sticky;top:0;z-index:1;padding:8px 0;background:#eff2e9;border-bottom:1px solid var(--line)}audio{width:100%;display:block;height:40px}#playing{overflow-wrap:anywhere;font-size:14px;margin:3px 0}.row{display:grid;grid-template-columns:1fr auto;gap:8px;align-items:center;border-bottom:1px solid var(--line);padding:8px 0}.meta{font-size:12px;color:#48625d;overflow-wrap:anywhere}.pages{display:flex;align-items:center;justify-content:space-between;gap:4px;padding:12px 0}button:disabled{opacity:.45;cursor:auto}a{font-size:13px}.count{font-variant-numeric:tabular-nums}
</style><main><div class="mast">NEKOCONNE / SOUND ARCHIVE</div><h1>语音档案</h1>
<p>按原始角色编号、用途或台词名查找。点击台词即可试听，下方链接打开原始 WAV。</p>
<div class="filters"><label class="search">搜索<input id="search" type="search" placeholder="如：10101、goodMorning、titleCall"></label>
<label>角色编号<select id="role"><option value="">全部</option></select></label><label>用途<select id="category"><option value="">全部</option></select></label></div>
<div class="bar"><p id="playing">尚未选择台词</p><audio id="player" controls preload="none"></audio></div>
<div class="pages"><button id="prev">上一页</button><span class="count" id="count" aria-live="polite"></span><button id="next">下一页</button></div><div id="results"></div></main>
<script type="application/json" id="library">%%DATA%%</script><script>
'use strict';const $=id=>document.getElementById(id),rows=JSON.parse($('library').textContent),size=50;let page=0,found=[];
const options=(el,arr)=>{for(const value of [...new Set(arr)].sort()){const o=new Option(value,value);el.add(o)}};
const searchable=rows.map(([path,sheet,cue])=>[path,sheet,cue].join(' ').toLowerCase());
options($('role'),rows.map(r=>r[0].split('/')[2]));options($('category'),rows.map(r=>r[0].split('/')[1]));
function apply(){const q=$('search').value.trim().toLowerCase().split(/\\s+/).filter(Boolean),role=$('role').value,cat=$('category').value;found=[];for(let i=0;i<rows.length;i++){const parts=rows[i][0].split('/');if((!cat||parts[1]===cat)&&(!role||parts[2]===role)&&q.every(v=>searchable[i].includes(v)))found.push(i)}page=0;draw()}
function draw(){const total=Math.ceil(found.length/size),view=document.createDocumentFragment();for(const index of found.slice(page*size,(page+1)*size)){const [path,sheet,cue,seconds]=rows[index],row=document.createElement('div'),detail=document.createElement('div'),b=document.createElement('button'),meta=document.createElement('div'),a=document.createElement('a');row.className='row';b.className='play';b.textContent=cue||sheet;b.onclick=()=>{$('player').src=path;$('playing').textContent=sheet+(cue!==sheet?' / '+cue:'');$('player').play().catch(()=>{$('playing').textContent+='（请使用播放控件）'})};meta.className='meta';meta.textContent=sheet+' · '+seconds+' s';a.href=path;a.textContent='WAV';detail.append(b,meta);row.append(detail,a);view.append(row)}$('results').replaceChildren(view);$('count').textContent=found.length.toLocaleString()+' 条 | '+(total?page+1:0)+' / '+total;$('prev').disabled=page<=0;$('next').disabled=page+1>=total}
$('search').oninput=apply;$('category').onchange=apply;$('role').onchange=apply;$('prev').onclick=()=>{page--;draw()};$('next').onclick=()=>{page++;draw()};apply();</script></html>'''


def generate(out):
    with (out / 'decoded-voices.csv').open(encoding='utf-8-sig', newline='') as f:
        entries = [[r['wav'], r['sheet'], r['cue'], round(int(r['frames']) / int(r['rate']), 2)] for r in csv.DictReader(f)]
    data = json.dumps(entries, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
    (out / 'listen.html').write_text(TEMPLATE.replace('%%DATA%%', data), encoding='utf-8')
    print(f'Indexed {len(entries)} local WAVs')


if __name__ == '__main__':
    generate(Path('exports/audio'))
