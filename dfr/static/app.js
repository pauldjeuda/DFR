const fmtTime = (value) => value ? new Date(value * 1000).toLocaleString('fr-FR') : 'Jamais';
const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

function filterParams() {
  const form = document.querySelector('#log-filters');
  const values = new FormData(form);
  const params = new URLSearchParams();
  for (const [key, value] of values.entries()) {
    if (!value) continue;
    if (key === 'from' || key === 'to') params.set(key, String(Math.floor(new Date(value).getTime() / 1000)));
    else params.set(key, value);
  }
  return params;
}

async function get(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Erreur HTTP ${response.status}`);
  return response.json();
}

async function refreshAll() {
  try {
    const params = filterParams();
    params.set('limit', '12');
    const [status, nodes, logs, conflicts] = await Promise.all([
      get('/api/status'), get('/api/nodes'), get('/api/logs?' + params), get('/api/conflicts')
    ]);
    document.querySelector('#node-id').textContent = status.node;
    document.querySelector('#queue').textContent = status.queue;
    document.querySelector('#last-sync').textContent = fmtTime(status.last_success);
    document.querySelector('#conflict-count').textContent = conflicts.length;
    const pill = document.querySelector('#service-state');
    pill.textContent = status.paused ? 'En pause' : 'En service';
    pill.classList.toggle('offline', !status.running);

    document.querySelector('#nodes').innerHTML = nodes.length ? nodes.map((node) =>
      `<article class="node"><div><strong>${escapeHtml(node.id)}</strong><small>Dernier succès : ${fmtTime(node.last_success)}</small><small>Écart d'horloge : ${Number(node.clock_delta || 0).toFixed(2)} s</small>${node.last_error ? `<small>${escapeHtml(node.last_error)}</small>` : ''}</div><span class="badge ${node.online ? '' : 'down'}">${node.online ? 'En ligne' : 'Hors ligne'}</span></article>`
    ).join('') : '<p class="muted">Aucun pair configuré.</p>';
    document.querySelector('#logs').innerHTML = logs.items.length ? logs.items.map((item) =>
      `<tr><td>${fmtTime(item.ts)}</td><td>${escapeHtml(item.event)}</td><td>${escapeHtml(item.path || '—')}</td><td>${escapeHtml(item.peer || item.node)}</td><td>${escapeHtml(item.result || '—')}</td></tr>`
    ).join('') : '<tr><td colspan="5">Aucune opération enregistrée.</td></tr>';
    document.querySelector('#conflicts').innerHTML = conflicts.length ? conflicts.slice(0, 8).map((item) =>
      `<div class="conflict"><strong>${escapeHtml(item.path)}</strong><small>Version retenue : ${escapeHtml(item.winner)} · version conservée : ${escapeHtml(item.loser)} · ${fmtTime(item.ts)}</small><button onclick="restoreConflict(${item.id})">Restaurer la version conservée</button></div>`
    ).join('') : '<p class="muted">Aucun conflit récent.</p>';
    for (const format of ['csv', 'json']) {
      const exportParams = filterParams(); exportParams.set('format', format);
      document.querySelector('#export-' + format).href = '/api/logs/export?' + exportParams;
    }
    document.querySelector('#refresh-state').textContent = 'Mis à jour ' + new Date().toLocaleTimeString('fr-FR');
  } catch (error) {
    document.querySelector('#refresh-state').textContent = 'Démon indisponible';
    document.querySelector('#service-state').textContent = 'Indisponible';
    document.querySelector('#service-state').classList.add('offline');
  }
}

async function control(action) {
  const message = document.querySelector('#action-message');
  try {
    const response = await fetch('/api/control/' + action, {method: 'POST'});
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || 'Action impossible');
    message.textContent = ({pause: 'Synchronisation mise en pause.', resume: 'Synchronisation reprise.', resync: 'Réconciliation ajoutée à la file.'})[action] || 'Action prise en compte.';
    refreshAll();
  } catch (error) { message.textContent = error.message; }
}

async function restoreConflict(id) {
  try {
    const response = await fetch(`/api/conflicts/${id}/restore`, {method: 'POST'});
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || 'Restauration impossible');
    document.querySelector('#action-message').textContent = 'Version restaurée et resynchronisation lancée.';
    refreshAll();
  } catch (error) { document.querySelector('#action-message').textContent = error.message; }
}

document.querySelector('#log-filters').addEventListener('submit', (event) => { event.preventDefault(); refreshAll(); });
document.querySelector('#log-filters').addEventListener('reset', () => setTimeout(refreshAll, 0));
refreshAll();
setInterval(refreshAll, 10000);
