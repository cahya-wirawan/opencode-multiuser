from html import escape

PORTAL_CSS = r'''
#oc-mu-portal{position:fixed;top:12px;right:14px;z-index:2147483647;font:13px/1.35 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#202124}
#oc-mu-portal *{box-sizing:border-box}
#oc-mu-portal>summary{list-style:none;display:flex;align-items:center;gap:7px;border:1px solid rgba(0,0,0,.16);border-radius:10px;background:rgba(255,255,255,.97);color:#334155;padding:8px 11px;box-shadow:0 8px 24px rgba(15,23,42,.12);backdrop-filter:blur(12px);cursor:pointer;user-select:none}
#oc-mu-portal>summary::-webkit-details-marker{display:none}
#oc-mu-portal .ocmu-dot{width:7px;height:7px;border-radius:50%;background:#10b981;box-shadow:0 0 0 3px rgba(16,185,129,.12);display:inline-block}
#oc-mu-portal .ocmu-menu{position:absolute;right:0;top:calc(100% + 7px);width:225px;border:1px solid rgba(0,0,0,.16);border-radius:12px;background:#fff;box-shadow:0 18px 45px rgba(15,23,42,.18);overflow:hidden}
#oc-mu-portal .ocmu-head{padding:10px 12px;border-bottom:1px solid #e5e7eb;background:#f8fafc;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:600}
#oc-mu-portal form{margin:0;padding:0}
#oc-mu-portal a,#oc-mu-portal button.ocmu-item{display:block;width:100%;border:0;border-radius:0;background:#fff;color:#202124;text-align:left;text-decoration:none;padding:10px 12px;font:inherit;cursor:pointer}
#oc-mu-portal a:hover,#oc-mu-portal button.ocmu-item:hover{background:#f1f5f9}
#oc-mu-portal .ocmu-danger{color:#b3261e!important;border-top:1px solid #e5e7eb!important}
#oc-mu-portal .ocmu-status{padding:7px 12px;color:#64748b;font-size:11px;border-top:1px solid #e5e7eb;background:#f8fafc}
#oc-mu-portal.ocmu-busy>summary{cursor:wait}#oc-mu-portal.ocmu-busy .ocmu-dot{background:#d97706;animation:ocmu-pulse 1s ease-in-out infinite}#oc-mu-portal.ocmu-busy a,#oc-mu-portal.ocmu-busy button{pointer-events:none;opacity:.55}
@keyframes ocmu-pulse{50%{opacity:.25}}
@media (prefers-color-scheme:dark){#oc-mu-portal{color:#e8eaed}#oc-mu-portal>summary,#oc-mu-portal .ocmu-menu,#oc-mu-portal a,#oc-mu-portal button.ocmu-item{background:#202124;color:#e8eaed;border-color:#4b4d50}#oc-mu-portal .ocmu-head,#oc-mu-portal .ocmu-status{background:#292a2d;border-color:#4b4d50;color:#bdc1c6}#oc-mu-portal a:hover,#oc-mu-portal button.ocmu-item:hover{background:#303134}#oc-mu-portal .ocmu-danger{border-color:#4b4d50!important;color:#f28b82!important}}
'''.strip()

PORTAL_JS = r'''
(() => {
  const root = document.getElementById('oc-mu-portal');
  if (!root || root.dataset.bound === '1') return;
  root.dataset.bound = '1';
  const status = root.querySelector('.ocmu-status');
  const dashboard = root.querySelector('a[data-action="dashboard"]');
  const stopForm = root.querySelector('form[data-action="stop"]');
  const logoutForm = root.querySelector('form[data-action="logout"]');

  function busy(message) {
    root.classList.add('ocmu-busy');
    root.open = true;
    if (status) status.textContent = message;
  }
  function submitWithPaint(form, message) {
    busy(message);
    requestAnimationFrame(() => requestAnimationFrame(() => form.submit()));
  }

  dashboard?.addEventListener('click', (e) => {
    e.preventDefault();
    busy('Opening dashboard…');
    requestAnimationFrame(() => requestAnimationFrame(() => window.location.assign('/dashboard')));
  });
  stopForm?.addEventListener('submit', (e) => {
    e.preventDefault();
    if (!window.confirm('Stop this workspace and free its slot?')) return;
    submitWithPaint(stopForm, 'Stopping workspace and freeing slot…');
  });
  logoutForm?.addEventListener('submit', (e) => {
    e.preventDefault();
    if (!window.confirm('Logout and stop your running workspace(s)?')) return;
    submitWithPaint(logoutForm, 'Logging out and freeing workspace slots…');
  });

  async function poll() {
    try {
      const r = await fetch('/_portal/status', {credentials: 'same-origin', cache: 'no-store'});
      if (r.status === 401) {
        window.location.assign('/login');
        return;
      }
      if (!r.ok) return;
      const data = await r.json();
      if (data.workspace_status === 'stopped' && data.stop_reason === 'idle') {
        const q = new URLSearchParams({reason: 'idle'});
        if (data.project_slug) q.set('workspace', data.project_slug);
        window.location.assign('/dashboard?' + q.toString());
        return;
      }
      if (data.workspace_status !== 'running') {
        window.location.assign('/dashboard');
        return;
      }
      if (status && !root.classList.contains('ocmu-busy')) status.textContent = `Running · slot ${data.slot_id ?? '-'}`;
    } catch (_) {
      // A transient gateway/network failure should not disrupt the OpenCode UI.
    }
  }
  window.setInterval(poll, 30000);
})();
'''.strip()


def portal_markup(project_slug: str) -> str:
    project = escape(project_slug, quote=True)
    return (
        f'<details id="oc-mu-portal" data-project="{project}">'
        '<summary aria-label="Open workspace portal menu"><span class="ocmu-dot"></span><span>Portal</span><span aria-hidden="true">▾</span></summary>'
        '<div class="ocmu-menu">'
        f'<div class="ocmu-head" title="{project}">{project}</div>'
        '<a href="/dashboard" data-action="dashboard">Dashboard</a>'
        '<form method="post" action="/_portal/stop" data-action="stop"><button class="ocmu-item" type="submit">Stop workspace</button></form>'
        '<form method="post" action="/_portal/logout" data-action="logout"><button class="ocmu-item ocmu-danger" type="submit">Logout</button></form>'
        '<div class="ocmu-status">Running</div>'
        '</div></details>'
        '<link rel="stylesheet" href="/_portal/widget.css">'
        '<script defer src="/_portal/widget.js"></script>'
    )


def inject_portal_widget(html: str, project_slug: str) -> str:
    if 'id="oc-mu-portal"' in html:
        return html
    snippet = portal_markup(project_slug)
    lower = html.lower()
    index = lower.rfind('</body>')
    if index >= 0:
        return html[:index] + snippet + html[index:]
    return html + snippet
