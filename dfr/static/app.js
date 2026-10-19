const fmtTime = (value) => value ? new Date(value * 1000).toLocaleString('fr-FR') : 'Jamais';
const fmtUptime = (seconds) => {
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return days ? `${days} j ${hours} h` : hours ? `${hours} h ${minutes} min` : `${minutes} min`;
};
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
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `Erreur HTTP ${response.status}`);
  return body;
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
    document.querySelector('#uptime').textContent = fmtUptime(status.uptime_s || 0);
    document.querySelector('#conflict-count').textContent = conflicts.length;
    const pill = document.querySelector('#service-state');
    pill.textContent = status.paused ? 'En pause' : 'En service';
    pill.classList.toggle('offline', !status.running);

    document.querySelector('#shared-root').textContent = status.root || 'Indisponible';
    document.querySelector('#nodes').innerHTML = nodes.length ? nodes.map((node) => {
      const live = node.reachable === true;
      const identityOK = node.identity_matches !== false;
      const delta = node.clock_delta_live ?? node.clock_delta;
      const healthy = live && identityOK;
      return `<article class="node"><div><strong>${escapeHtml(node.id)}</strong><small>Nœud distant : ${escapeHtml(node.reported_node || 'non confirmé')}</small><small>Vérifié : ${fmtTime(node.checked_at)}</small><small>Dernier succès de réplication : ${fmtTime(node.last_success)}</small><small>Écart d'horloge : ${Number(delta || 0).toFixed(2)} s</small>${!identityOK ? '<small class="error-text">L’identifiant distant ne correspond pas au pair configuré.</small>' : ''}${node.probe_error ? `<small class="error-text">${escapeHtml(node.probe_error)}</small>` : ''}${node.last_error ? `<small>${escapeHtml(node.last_error)}</small>` : ''}</div><span class="badge ${healthy ? '' : 'down'}">${!live ? 'Injoignable' : identityOK ? 'Joignable maintenant' : 'Identité inattendue'}</span></article>`;
    }).join('') : '<p class="muted">Aucun pair configuré.</p>';
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

let currentDirectory = '';
let editingPath = '';

function fileMessage(message, isError = false) {
  const element = document.querySelector('#files-message');
  element.textContent = message;
  element.classList.toggle('error-text', isError);
}

async function apiRequest(url, options = {}) {
  const response = await fetch(url, options);
  const body = response.headers.get('content-type')?.includes('application/json') ? await response.json() : {};
  if (!response.ok) throw new Error(body.error || `Erreur HTTP ${response.status}`);
  return body;
}

function renderBreadcrumbs() {
  const pieces = currentDirectory ? currentDirectory.split('/') : [];
  const crumb = document.querySelector('#file-breadcrumbs');
  crumb.innerHTML = `<button type="button" data-dir="">Racine partagée</button>` + pieces.map((piece, index) => {
    const path = pieces.slice(0, index + 1).join('/');
    return `<span aria-hidden="true">/</span><button type="button" data-dir="${escapeHtml(path)}">${escapeHtml(piece)}</button>`;
  }).join('');
}

function humanSize(bytes) {
  if (bytes < 1024) return `${bytes} o`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} Kio`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} Mio`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} Gio`;
}

async function loadFiles(path = currentDirectory) {
  try {
    const listing = await get('/api/files?' + new URLSearchParams({path}));
    currentDirectory = listing.path;
    renderBreadcrumbs();
    document.querySelector('#shared-root').textContent = listing.root;
    document.querySelector('#files').innerHTML = listing.items.length ? listing.items.map((item) => {
      const canOpen = item.type === 'directory';
      const canEdit = item.type === 'file';
      const open = canOpen ? `<button type="button" data-action="open" data-path="${escapeHtml(item.path)}">Ouvrir</button>` : '';
      const edit = canEdit ? `<button type="button" data-action="edit" data-path="${escapeHtml(item.path)}">Modifier</button><a class="button-link" href="/api/files/download?${new URLSearchParams({path: item.path})}">Télécharger</a>` : '';
      const move = item.type !== 'symlink' ? `<button type="button" data-action="move" data-path="${escapeHtml(item.path)}">Déplacer ici</button>` : '';
      return `<tr><td>${escapeHtml(item.name)}</td><td>${({directory:'Répertoire',file:'Fichier',symlink:'Lien symbolique'})[item.type] || item.type}</td><td>${item.type === 'file' ? humanSize(item.size) : '—'}</td><td>${fmtTime(item.modified)}</td><td class="file-actions">${open}${edit}<button type="button" data-action="rename" data-path="${escapeHtml(item.path)}">Renommer</button>${move}<button type="button" data-action="delete" data-path="${escapeHtml(item.path)}" class="danger">Supprimer</button></td></tr>`;
    }).join('') : '<tr><td colspan="5">Ce répertoire est vide.</td></tr>';
  } catch (error) {
    document.querySelector('#files').innerHTML = `<tr><td colspan="5">${escapeHtml(error.message)}</td></tr>`;
    fileMessage(error.message, true);
  }
}

async function loadConfiguredPeers() {
  try {
    const peers = await get('/api/config/peers');
    document.querySelector('#configured-peers').innerHTML = peers.length ? peers.map((peer) =>
      `<div class="configured-peer"><span><strong>${escapeHtml(peer.id)}</strong><small>${escapeHtml(peer.target)}</small></span><button type="button" data-remove-peer="${escapeHtml(peer.id)}" class="danger">Retirer</button></div>`
    ).join('') : '<p class="muted">Aucun pair configuré.</p>';
  } catch (error) {
    document.querySelector('#configured-peers').textContent = error.message;
  }
}

async function savePeers(peers) {
  const message = document.querySelector('#peer-message');
  try {
    const result = await apiRequest('/api/config/peers', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({peers})});
    message.textContent = 'Configuration enregistrée. Le démon recharge le maillage.';
    message.classList.remove('error-text');
    await loadConfiguredPeers();
    refreshAll();
    return result;
  } catch (error) {
    message.textContent = error.message;
    message.classList.add('error-text');
    throw error;
  }
}

document.querySelector('#peer-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const values = new FormData(form);
  try {
    const peers = await get('/api/config/peers');
    peers.push({id: values.get('id'), target: values.get('target')});
    await savePeers(peers);
    form.reset();
  } catch (error) {
    const message = document.querySelector('#peer-message');
    if (!message.textContent) message.textContent = error.message;
    message.classList.add('error-text');
  }
});

document.querySelector('#configured-peers').addEventListener('click', async (event) => {
  const button = event.target.closest('button[data-remove-peer]');
  if (!button || !confirm(`Retirer le pair « ${button.dataset.removePeer} » du maillage ?`)) return;
  try {
    const peers = await get('/api/config/peers');
    await savePeers(peers.filter((peer) => peer.id !== button.dataset.removePeer));
  } catch (error) {
    const message = document.querySelector('#peer-message');
    message.textContent = error.message;
    message.classList.add('error-text');
  }
});

document.querySelector('#file-breadcrumbs').addEventListener('click', (event) => {
  const button = event.target.closest('button[data-dir]');
  if (button) loadFiles(button.dataset.dir);
});
document.querySelector('#files-refresh').addEventListener('click', () => loadFiles());
document.querySelector('#files').addEventListener('click', async (event) => {
  const button = event.target.closest('button[data-action]');
  if (!button) return;
  const {action, path} = button.dataset;
  try {
    if (action === 'open') return loadFiles(path);
    if (action === 'edit') {
      const result = await apiRequest('/api/files/content?' + new URLSearchParams({path}));
      editingPath = result.path;
      document.querySelector('#editor-title').textContent = `Modifier ${result.path}`;
      document.querySelector('#editor-content').value = result.content;
      document.querySelector('#file-editor').hidden = false;
      document.querySelector('#editor-content').focus();
      return;
    }
    if (action === 'delete') {
      if (!confirm(`Supprimer « ${path} »${button.closest('tr').querySelector('td:nth-child(2)').textContent === 'Répertoire' ? ' et tout son contenu' : ''} ?`)) return;
      await apiRequest('/api/files?' + new URLSearchParams({path}), {method: 'DELETE'});
    }
    if (action === 'rename') {
      const newName = prompt('Nouveau nom :', path.split('/').pop());
      if (!newName || newName === path.split('/').pop()) return;
      const destination = path.includes('/') ? path.slice(0, path.lastIndexOf('/') + 1) + newName : newName;
      await apiRequest('/api/files/rename', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path, destination})});
    }
    if (action === 'move') {
      const destination = currentDirectory ? `${currentDirectory}/${path.split('/').pop()}` : path.split('/').pop();
      if (destination === path || !confirm(`Déplacer « ${path} » dans « ${currentDirectory || 'la racine partagée'} » ?`)) return;
      await apiRequest('/api/files/rename', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path, destination})});
    }
    fileMessage('Opération enregistrée. La réplication se fera automatiquement.');
    await loadFiles();
  } catch (error) { fileMessage(error.message, true); }
});

document.querySelector('#new-directory-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    await apiRequest('/api/files/directories', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({parent: currentDirectory, name: new FormData(form).get('name')})});
    form.reset(); fileMessage('Répertoire créé et ajouté à la réplication.'); await loadFiles();
  } catch (error) { fileMessage(error.message, true); }
});

document.querySelector('#new-file-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    await apiRequest('/api/files', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({parent: currentDirectory, name: new FormData(form).get('name'), content: ''})});
    form.reset(); fileMessage('Fichier créé et ajouté à la réplication.'); await loadFiles();
  } catch (error) { fileMessage(error.message, true); }
});

document.querySelector('#upload-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  form.set('parent', currentDirectory);
  try {
    const result = await apiRequest('/api/files/upload', {method: 'POST', body: form});
    event.currentTarget.reset(); fileMessage(`${result.created.length} fichier(s) importé(s) et ajouté(s) à la réplication.`); await loadFiles();
  } catch (error) { fileMessage(error.message, true); }
});

document.querySelector('#editor-close').addEventListener('click', () => { document.querySelector('#file-editor').hidden = true; });
document.querySelector('#editor-save').addEventListener('click', async () => {
  try {
    await apiRequest('/api/files/content', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path: editingPath, content: document.querySelector('#editor-content').value})});
    document.querySelector('#file-editor').hidden = true;
    fileMessage('Modifications enregistrées et ajoutées à la réplication.');
    await loadFiles();
  } catch (error) { fileMessage(error.message, true); }
});

loadFiles();
loadConfiguredPeers();
refreshAll();
setInterval(refreshAll, 10000);
