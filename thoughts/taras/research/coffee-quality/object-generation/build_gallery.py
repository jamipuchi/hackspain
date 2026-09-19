"""Build the local visual comparison gallery from saved experiment results."""

from __future__ import annotations

import html
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
MODELS = ("flash-lite", "deepseek", "glm", "qwen", "gemini")
MODEL_NAMES = {"flash-lite": "Gemini 2.5 Flash Lite", "deepseek": "DeepSeek V4.1 Flash",
               "glm": "GLM 5.3", "qwen": "Qwen3.8 Max", "gemini": "Gemini 3.8 Flash"}
CASES = ("earring", "star", "logo")
CASE_NAMES = {"earring": "Earring", "star": "Star", "logo": "NOVA badge"}
REPAIRS = {
    ("deepseek", "logo"): {"folder": RESULTS / "deepseek-repair" / "logo", "label": "Retry: 16k token limit", "failure": "Initial attempt reached its token limit", "total_api": True},
    ("qwen", "star"): {"folder": RESULTS / "qwen-repair" / "star", "label": "Normalized duplicate closure", "failure": "Initial outline failed duplicate-vertex validation", "total_api": False},
}


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def escaped(value):
    return html.escape(str(value), quote=True)


def relative(path):
    return escaped(path.relative_to(HERE).as_posix()) if path.exists() else ""


def number(value, digits=2, prefix=""):
    if isinstance(value, (int, float)):
        return f"{prefix}{value:.{digits}f}"
    return "Unavailable"


def cost(value):
    if not isinstance(value, (int, float)):
        return "Unavailable"
    return f"${value:.6f}".rstrip("0").rstrip(".")


def failure_reason(response, execution, render):
    if isinstance(response, dict):
        if response.get("error"):
            error = response["error"]
            return f"Provider status: {error.get('message', error)}" if isinstance(error, dict) else f"Provider status: {error}"
        choices = response.get("response", {}).get("choices", [])
        if choices:
            finish_reason = choices[0].get("finish_reason")
            if finish_reason and finish_reason != "stop":
                return f"Finish reason: {finish_reason}"
    if isinstance(render, dict) and render.get("returncode") not in (None, 0):
        return f"Render status: exit {render['returncode']}"
    if isinstance(execution, dict) and execution.get("returncode") not in (None, 0):
        return f"Recipe status: exit {execution['returncode']}"
    return "Saved output is missing"


def links_for(folder, perspective, top, glb, original_response=""):
    recipe = relative(folder / "recipe.json")
    image = perspective or top
    items = []
    if recipe:
        items.append(f'<a href="{recipe}" target="_blank">Recipe</a>')
    if image:
        items.append(f'<a class="png-link" href="{image}" target="_blank">Full image</a>')
    if glb:
        items.append(f'<a href="{glb}" target="_blank">GLB</a>')
    if original_response:
        items.append(f'<a href="{original_response}" target="_blank">Original JSON</a>')
    return "".join(items) if items else '<span class="quiet">No files</span>'


def verdict_for(verdicts, model_key, case):
    model_verdicts = verdicts.get(model_key)
    if isinstance(model_verdicts, dict):
        return model_verdicts.get(case)
    return verdicts.get(f"{model_key}/{case}")


def card(model_key, case, verdicts):
    folder = RESULTS / model_key / case
    response_record = read_json(folder / "openrouter_response.json")
    execution = read_json(folder / "execution.json")
    render_folder = folder / "render"
    render = read_json(render_folder / "render.json")
    perspective = relative(render_folder / "perspective.png")
    top = relative(render_folder / "top.png")
    glb = relative(render_folder / "object.glb")
    repair = REPAIRS.get((model_key, case))
    repair_used = False
    repair_note = ""
    active_folder = folder
    if repair:
        repair_folder = repair["folder"]
        repair_render_folder = repair_folder / "render"
        repair_render = read_json(repair_render_folder / "render.json")
        repair_perspective = relative(repair_render_folder / "perspective.png")
        repair_top = relative(repair_render_folder / "top.png")
        if repair_perspective and repair_top and isinstance(repair_render, dict):
            active_folder = repair_folder
            render = repair_render
            perspective = repair_perspective
            top = repair_top
            glb = relative(repair_render_folder / "object.glb")
            repair_used = True
            repair_note = f'<span class="repair-note">{escaped(repair["label"])}</span>'
    response = response_record.get("response", {}) if isinstance(response_record, dict) else {}
    model_name = response.get("model") if isinstance(response, dict) else None
    if not model_name and isinstance(execution, dict):
        model_name = execution.get("model")
    model_name = model_name or model_key
    usage = response.get("usage", {}) if isinstance(response, dict) else {}
    api_seconds = response_record.get("latency_s") if isinstance(response_record, dict) else None
    api_label = "API"
    billed_label = "Billed"
    if repair and repair["total_api"]:
        repair_response = read_json(repair["folder"] / "openrouter_response.json")
        retry = repair_response.get("response", {}) if isinstance(repair_response, dict) else {}
        retry_usage = retry.get("usage", {}) if isinstance(retry, dict) else {}
        retry_seconds = repair_response.get("latency_s") if isinstance(repair_response, dict) else None
        retry_cost = retry_usage.get("cost") if isinstance(retry_usage, dict) else None
        if isinstance(api_seconds, (int, float)) and isinstance(retry_seconds, (int, float)):
            api_seconds += retry_seconds
            api_label = "API total"
        if isinstance(usage.get("cost"), (int, float)) and isinstance(retry_cost, (int, float)):
            usage = {"cost": usage["cost"] + retry_cost}
            billed_label = "Billed total"
    render_seconds = render.get("render_timings_seconds", {}).get("total") if isinstance(render, dict) else None
    ready = bool(perspective and top and isinstance(render, dict))
    verdict = verdict_for(verdicts, model_key, case)
    title = escaped(MODEL_NAMES[model_key])
    image = (
        f'<img class="object-image" src="{perspective}" data-perspective="{perspective}" data-top="{top}" '
        f'data-real-alt="{escaped(f"{CASE_NAMES[case]} render from {model_name}")}" '
        f'alt="{escaped(f"{CASE_NAMES[case]} render from {model_name}")}">' if ready else ""
    )
    state = '<span class="state ready">Rendered</span>' if ready else f'<span class="state missing">{escaped(failure_reason(response_record, execution, render))}</span>'
    if repair_used:
        state = f'<span class="state missing">{escaped(repair["failure"])}</span>'
    original_failure = f'<a href="{relative(folder / "execution.json")}" target="_blank">Original failure</a>' if repair_used else ""
    critique = f'<p class="critique">{escaped(verdict)}</p>' if isinstance(verdict, str) and verdict.strip() else ""
    return f'''<article class="result-card" data-model="{escaped(model_key)}">
  <div class="card-head">
    <h3 class="model-name" data-real-label="{title}">{title}</h3>
    <span class="blind-label" aria-hidden="true"></span>
  </div>
  {state}{repair_note}
  <div class="image-wrap">{image or '<div class="missing-image">No rendered image</div>'}</div>
  <dl class="metrics">
    <div><dt>{api_label}</dt><dd>{number(api_seconds)} s</dd></div>
    <div><dt>{billed_label}</dt><dd>{cost(usage.get('cost'))}</dd></div>
    <div><dt>Render</dt><dd>{number(render_seconds)} s</dd></div>
  </dl>
  {critique}
  <nav class="links" aria-label="Saved files">{links_for(active_folder, perspective, top, glb, relative(folder / 'openrouter_response.json') if repair_used else '')}{original_failure}</nav>
</article>'''


def page(verdicts):
    rows = "".join(
        f'''<section class="case-row" data-case="{case}">
  <header class="case-heading"><h2>{CASE_NAMES[case]}</h2><p>Original outputs; repairs are labeled</p></header>
  <div class="comparison-grid">{''.join(card(model, case, verdicts) for model in MODELS)}</div>
</section>'''
        for case in CASES
    )
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Object recipe comparison</title>
<style>
:root {{ color-scheme: light; --paper:#fbf7ef; --ink:#292721; --rule:#d7d0c3; --muted:#716b60; --accent:#49666a; --panel:#fffdf8; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--paper); color:var(--ink); font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
main {{ max-width:1800px; margin:0 auto; padding:36px 26px 64px; }}
.masthead {{ display:flex; justify-content:space-between; align-items:end; gap:24px; padding-bottom:22px; border-bottom:2px solid var(--ink); }}
h1,h2,h3,p {{ margin:0; }}
h1 {{ font-family:Georgia,"Times New Roman",serif; font-size:clamp(2rem,4vw,3.5rem); font-weight:500; letter-spacing:-.04em; }}
.intro {{ max-width:54ch; margin-top:10px; color:var(--muted); line-height:1.45; }}
.controls {{ display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end; }}
button {{ border:1px solid var(--accent); border-radius:3px; background:transparent; color:var(--accent); cursor:pointer; font:inherit; padding:8px 10px; }}
button[aria-pressed="true"] {{ background:var(--accent); color:white; }}
button:focus-visible,a:focus-visible {{ outline:3px solid #b7d1d2; outline-offset:2px; }}
.case-row {{ padding:28px 0 8px; border-bottom:1px solid var(--rule); }}
.case-heading {{ display:flex; align-items:baseline; justify-content:space-between; gap:16px; padding-bottom:12px; }}
h2 {{ font-size:1.15rem; font-weight:650; }}
.case-heading p,.quiet {{ color:var(--muted); font-size:.86rem; }}
.comparison-grid {{ display:grid; grid-template-columns:repeat(5,minmax(180px,1fr)); gap:12px; min-width:1040px; }}
.case-row {{ overflow-x:auto; }}
.result-card {{ min-width:0; min-height:365px; padding:12px; background:var(--panel); border:1px solid var(--rule); display:flex; flex-direction:column; }}
.card-head {{ display:flex; justify-content:space-between; gap:8px; min-height:38px; }}
.model-name {{ font-size:.88rem; line-height:1.25; font-weight:650; overflow-wrap:anywhere; }}
.blind-label {{ color:var(--accent); font-weight:700; }}
.state {{ display:block; min-height:20px; margin:4px 0 8px; font-size:.75rem; line-height:1.25; }}
.ready {{ color:#346244; }} .missing {{ color:#8a453b; }}
.repair-note {{ display:inline-block; margin:-4px 0 8px; padding:4px 6px; background:#d8e4e3; color:#294a4e; font-size:.74rem; font-weight:700; line-height:1.25; }}
.image-wrap {{ aspect-ratio:1; background:#ebe6dc; display:grid; place-items:center; overflow:hidden; }}
.object-image {{ width:100%; height:100%; object-fit:cover; }}
.missing-image {{ padding:16px; text-align:center; color:var(--muted); font-size:.85rem; line-height:1.35; }}
.metrics {{ display:grid; grid-template-columns:repeat(3,1fr); gap:6px; margin:11px 0 0; }}
.metrics div {{ min-width:0; }}
dt {{ color:var(--muted); font-size:.68rem; }} dd {{ margin:2px 0 0; font-size:.78rem; font-variant-numeric:tabular-nums; overflow-wrap:anywhere; }}
.critique {{ margin:10px 0 0; color:var(--muted); font-size:.78rem; line-height:1.35; }}
.links {{ display:flex; gap:9px; flex-wrap:wrap; margin-top:auto; padding-top:12px; }}
a {{ color:var(--accent); font-size:.78rem; text-underline-offset:2px; }}
body.blind .model-name {{ display:none; }} body:not(.blind) .blind-label {{ display:none; }}
body.blind .metrics,body.blind .critique,body.blind .links {{ display:none; }}
@media (max-width:700px) {{ main {{ padding:24px 14px 42px; }} .masthead {{ align-items:start; flex-direction:column; }} .controls {{ justify-content:flex-start; }} .case-heading {{ align-items:start; flex-direction:column; gap:3px; }} }}
</style>
</head>
<body>
<main>
  <header class="masthead">
    <div><h1>Object recipe comparison</h1><p class="intro">Five model outputs, built by one frozen renderer. Select the view before a taste review.</p></div>
    <div class="controls" aria-label="Gallery controls">
      <button type="button" data-view="perspective" aria-pressed="true">Perspective</button>
      <button type="button" data-view="top" aria-pressed="false">Top</button>
      <button type="button" id="blind-toggle" aria-pressed="false">Blind labels</button>
    </div>
  </header>
  {rows}
</main>
<script>
const buttons = document.querySelectorAll('[data-view]');
const images = document.querySelectorAll('.object-image');
const pngLinks = document.querySelectorAll('.png-link');
function setView(view) {{
  buttons.forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.view === view)));
  images.forEach((image) => image.src = image.dataset[view]);
  pngLinks.forEach((link) => link.href = link.closest('.result-card').querySelector('.object-image').dataset[view]);
}}
buttons.forEach((button) => button.addEventListener('click', () => setView(button.dataset.view)));
document.querySelector('#blind-toggle').addEventListener('click', (event) => {{
  document.body.classList.toggle('blind');
  images.forEach((image) => image.alt = document.body.classList.contains('blind') ? 'Rendered object' : image.dataset.realAlt);
  event.currentTarget.setAttribute('aria-pressed', String(document.body.classList.contains('blind')));
}});
document.querySelectorAll('.comparison-grid').forEach((grid) => grid.querySelectorAll('.blind-label').forEach((label, index) => label.textContent = String.fromCharCode(65 + index)));
const selectedCase = new URLSearchParams(location.search).get('case');
if (['earring', 'star', 'logo'].includes(selectedCase)) {{
  document.querySelectorAll('.case-row').forEach((row) => row.hidden = row.dataset.case !== selectedCase);
}}
</script>
</body>
</html>'''


def main():
    verdicts = read_json(HERE / "verdicts.json")
    (HERE / "gallery.html").write_text(page(verdicts if isinstance(verdicts, dict) else {}), encoding="utf-8")


if __name__ == "__main__":
    main()
