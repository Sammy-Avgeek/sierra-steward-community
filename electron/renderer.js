const $ = id => document.getElementById(id);
const fields = [
  ['Discord', true, [
    ['CLIENT_ID', 'Application ID'], ['GUILD_ID', 'Server ID'], ['WELCOME_CHANNEL_ID', 'Welcome channel ID'],
    ['MOD_LOG_CHANNEL_ID', 'Private mod-log channel ID'], ['MEMBER_ROLE_ID', 'Default member role ID'],
  ]],
  ['Welcome and goodbye', false, [
    ['WELCOME_ENABLED', 'Automatic welcome', 'boolean'], ['GOODBYE_ENABLED', 'Automatic goodbye', 'boolean'],
    ['GOODBYE_CHANNEL_ID', 'Goodbye channel ID (optional)'], ['WELCOME_TITLE', 'Welcome title'],
    ['WELCOME_COLOR', 'Welcome hex colour'], ['GOODBYE_TITLE', 'Goodbye title'], ['GOODBYE_COLOR', 'Goodbye hex colour'],
    ['WELCOME_IMAGE_URL', 'Welcome image URL (optional)'], ['GOODBYE_IMAGE_URL', 'Goodbye image URL (optional)'],
    ['WELCOME_DESCRIPTION', 'Welcome message ({member}, {server})', 'textarea'],
    ['GOODBYE_DESCRIPTION', 'Goodbye message ({member}, {server})', 'textarea'],
  ]],
  ['Automated alerts', false, [
    ['FLIGHT_NEWS_CHANNEL_ID', 'Flight sim news channel ID'], ['SPECIAL_OFFERS_CHANNEL_ID', 'Special offers channel ID'],
    ['STEAM_STORE_COUNTRY', 'Steam store country (GB by default)'], ['CREATOR_ALERT_CHANNEL_ID', 'YouTube alert channel ID'],
    ['YOUTUBE_CHANNEL_IDS', 'YouTube channel IDs (comma-separated)'], ['SPAM_TRAP_CHANNEL_ID', 'Spam-trap channel ID'],
    ['SPAM_TRAP_MODE', 'Spam trigger (all or activity)'], ['SPAM_TRAP_TIMEOUT_MINUTES', 'Trap timeout minutes'],
  ]],
  ['Roles and tickets', false, [
    ['SELF_ROLE_IDS', 'Self-assignable role IDs (comma-separated)'], ['TICKET_CATEGORY_ID', 'Ticket category ID'],
    ['TICKET_STAFF_ROLE_ID', 'Ticket staff role ID'], ['TICKET_PANEL_TITLE', 'Ticket panel title'],
    ['TICKET_PANEL_DESCRIPTION', 'Ticket panel description', 'textarea'], ['TICKET_TYPES_JSON', 'Ticket types JSON', 'textarea'],
  ]],
];

function navigate(view) {
  for (const node of document.querySelectorAll('main > section')) node.hidden = node.id !== view;
  for (const button of document.querySelectorAll('[data-view]')) {
    if (button.dataset.view === view) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  }
}

const controls = new Map();
for (const [group, open, entries] of fields) {
  const details = document.createElement('details');
  details.open = open;
  const title = document.createElement('summary');
  title.textContent = group;
  const grid = document.createElement('div');
  grid.className = 'fields';
  for (const [key, name, kind] of entries) {
    const label = document.createElement('label');
    label.textContent = name;
    const input = document.createElement(kind === 'textarea' ? 'textarea' : kind === 'boolean' ? 'select' : 'input');
    if (kind === 'boolean') input.replaceChildren(new Option('On', 'true'), new Option('Off', 'false'));
    if (kind === 'textarea') label.className = 'settings-text';
    if (key.endsWith('_ID')) input.inputMode = 'numeric';
    label.append(input);
    grid.append(label);
    controls.set(key, input);
  }
  details.append(title, grid);
  $('settings-fields').append(details);
}

async function request(action, data, notice) {
  try {
    const response = await window.sierra.request(action, data);
    if (response.error) throw new Error(response.error);
    return response.value;
  } catch (error) {
    notice.textContent = error.message;
    return undefined;
  }
}

function showStatus(value) {
  $('status').textContent = value;
  $('status').dataset.state = value;
  $('start').disabled = !['Stopped', 'Failed'].includes(value);
  $('stop').disabled = !['Connecting', 'Online'].includes(value);
  $('restart').disabled = value !== 'Online';
}

function showSettings(saved) {
  for (const [key, input] of controls) {
    input.value = input.tagName === 'SELECT' ? (saved.fields[key]?.trim().toLowerCase() || 'true') : saved.fields[key] || '';
  }
  $('token').value = '';
  $('token-state').textContent = saved.hasToken ? 'Token saved. Leave this field blank to keep it.' : 'Enter your token from the Discord Developer Portal.';
}

async function loadSettings() {
  const saved = await request('load', {}, $('settings-note'));
  if (saved) showSettings(saved);
  return saved;
}

const previewRequest = () => ({json: $('embed-json').value, index: Math.max(0, $('message-select').selectedIndex)});
async function preview(resetSelection = false, data = previewRequest()) {
  if (resetSelection) data.index = 0;
  const draft = await request('preview', data, $('embed-note'));
  $('embed-preview').replaceChildren();
  if (!draft) return;
  $('message-select').replaceChildren(...draft.names.map(name => new Option(name)));
  $('message-select').selectedIndex = data.index;
  $('message-select').disabled = draft.names.length < 2;
  if (draft.message.content) {
    const text = document.createElement('p');
    text.textContent = draft.message.content;
    $('embed-preview').append(text);
  }
  for (const embed of draft.message.embeds) {
    const card = document.createElement('div');
    card.className = 'embed-card';
    const title = document.createElement('strong');
    title.textContent = embed.title || embed.author?.name || 'Embed';
    const description = document.createElement('p');
    description.textContent = [embed.description, ...(embed.fields || []).map(field => `${field.name}\n${field.value}`), embed.footer?.text].filter(Boolean).join('\n\n');
    card.append(title, description);
    $('embed-preview').append(card);
  }
  $('embed-note').textContent = `${draft.message.embeds.length} embeds ready to review.`;
  return draft;
}

let templates = [];
let personalEdition = false;
let selectedLogo = '';
function showBranding(branding) {
  $('product').textContent = branding.name;
  $('brand-logo').src = branding.logo || 'logo.png';
  document.title = branding.name;
  $('brand-name').value = branding.name;
  selectedLogo = branding.logo;
}
$('brand-file').onchange = () => {
  const file = $('brand-file').files[0];
  if (!file) return;
  if (!['image/png', 'image/jpeg'].includes(file.type) || file.size > 1_250_000) {
    $('brand-note').textContent = 'Choose a PNG or JPEG logo under 1.25 MB.';
    return;
  }
  const reader = new FileReader();
  reader.onload = () => { selectedLogo = reader.result; $('brand-note').textContent = `${file.name} selected.`; };
  reader.readAsDataURL(file);
};
for (const [button, action] of [['save-brand', 'save-branding'], ['apply-brand', 'apply-branding']]) {
  $(button).onclick = async () => {
    if (action === 'apply-branding' && !window.confirm('Update the saved Discord bot’s public name and logo?')) return;
    $(button).disabled = true;
    const result = await request(action, {name: $('brand-name').value, logo: selectedLogo}, $('brand-note'));
    if (result) {
      if (action === 'save-branding') showBranding(result);
      $('brand-note').textContent = action === 'save-branding' ? 'Dashboard branding saved.' : result;
    }
    $(button).disabled = false;
  };
}
window.sierra.onStatus(showStatus);
window.sierra.onActivity(line => {
  const log = $('activity');
  log.textContent = (log.textContent + (log.textContent.endsWith('.') ? '\n' : '') + line).slice(-18_000);
  log.scrollTop = log.scrollHeight;
});
for (const button of document.querySelectorAll('[data-view]')) button.onclick = () => navigate(button.dataset.view);
for (const action of ['start', 'stop', 'restart']) $(action).onclick = async () => {
  const phase = await request(action, {}, $('activity'));
  if (phase) showStatus(phase);
};
$('check').onclick = async () => {
  $('check').disabled = true;
  $('report-panel').hidden = false;
  $('report').textContent = 'Checking Discord…';
  const report = await request('check', {}, $('report'));
  if (report) $('report').textContent = report;
  $('check').disabled = false;
};
$('settings-form').onsubmit = async event => {
  event.preventDefault();
  $('save').disabled = true;
  const values = Object.fromEntries([...controls].map(([key, input]) => [key, input.value]));
  const saved = await request('save', {fields: values, token: $('token').value}, $('settings-note'));
  if (saved) {
    showSettings(saved);
    $('settings-note').textContent = 'Settings saved. Start or restart the bot to apply them.';
  }
  $('save').disabled = false;
};
$('reload').onclick = loadSettings;
$('developer-portal').onclick = () => request('developer-portal', {}, $('settings-note'));
$('preview').onclick = () => preview(true);
$('message-select').onchange = () => preview();
$('embed-json').oninput = () => { $('message-select').replaceChildren(); $('message-select').disabled = true; };
$('load-template').onclick = () => {
  const template = templates[$('templates').selectedIndex];
  if (!template) return;
  if ($('embed-json').value.trim() && !window.confirm('Replace the current embed draft?')) return;
  $('embed-json').value = JSON.stringify(template.data, null, 2);
  $('destination').value = template.channel_id ? String(template.channel_id) : '';
  preview(true);
};
$('channels').onclick = async () => {
  const channels = await request('channels', {}, $('embed-note'));
  if (!channels) return;
  const old = $('destination').value;
  $('destination').replaceChildren(new Option('Choose a channel', ''), ...channels.map(channel => new Option(`#${channel.name}`, channel.id)));
  if (channels.some(channel => channel.id === old)) $('destination').value = old;
};
for (const [button, event] of [['preview-welcome', 'WELCOME'], ['preview-goodbye', 'GOODBYE']]) {
  $(button).onclick = async () => {
    const fields = Object.fromEntries([...controls].filter(([key]) => key.startsWith(event)).map(([key, input]) => [key, input.value]));
    const draft = await request('member-preview', {event, fields, server: personalEdition ? 'Sierra Originals' : 'Your server'}, $('member-note'));
    if (!draft) return;
    const card = document.createElement('div');
    card.className = 'embed-card';
    const title = document.createElement('strong');
    title.textContent = draft.embed.title;
    const text = document.createElement('p');
    text.textContent = draft.embed.description;
    card.append(title, text);
    if (draft.banner) {
      const image = new Image();
      image.className = 'member-banner';
      image.src = `${draft.banner}.png`;
      image.alt = '';
      card.append(image);
    }
    $('member-note').textContent = draft.embed.image?.url ? 'The saved image URL will replace the built-in banner in Discord.' : '';
    $('member-preview').replaceChildren(card);
  };
}
$('ticket-channels').onclick = async () => {
  const channels = await request('channels', {}, $('ticket-note'));
  if (channels) {
    $('ticket-destination').replaceChildren(new Option('Choose a channel', ''), ...channels.map(channel => new Option(`#${channel.name}`, channel.id)));
  }
};
for (const [button, update] of [['ticket-update', true], ['ticket-post', false]]) {
  $(button).onclick = async () => {
    const channel = $('ticket-destination').value;
    if (!channel) { $('ticket-note').textContent = 'Load channels and choose the support channel.'; return; }
    const label = $('ticket-destination').selectedOptions[0].textContent;
    if (!window.confirm(`${update ? 'Update the existing support panel in' : 'Publish a new support panel to'} ${label}?`)) return;
    $('ticket-note').textContent = 'Contacting Discord…';
    $('ticket-update').disabled = true;
    $('ticket-post').disabled = true;
    try {
      const url = await request('ticket-panel', {channel, update}, $('ticket-note'));
      if (url) {
        const view = document.createElement('button');
        view.textContent = 'Open support panel';
        view.onclick = () => request('open-discord', {url}, $('ticket-note'));
        $('ticket-note').replaceChildren(document.createTextNode(update ? 'Panel updated. ' : 'Panel posted. '), view);
      }
    } finally {
      $('ticket-update').disabled = $('ticket-post').disabled = false;
    }
  };
}
$('ticket-refresh').onclick = async () => {
  const tickets = await request('tickets', {}, $('ticket-note'));
  if (!tickets) return;
  $('ticket-note').textContent = tickets.length ? '' : 'No tickets have been recorded yet.';
  $('ticket-list').replaceChildren(...tickets.map(ticket => {
    const row = document.createElement('li');
    row.textContent = `#${ticket.number} · ${ticket.type} · ${ticket.open ? 'Open' : 'Closed'}`;
    if (ticket.channel) {
      const view = document.createElement('button');
      view.textContent = 'Open in Discord';
      view.onclick = () => request('open-discord', {url: `https://discord.com/channels/${ticket.guild}/${ticket.channel}`}, $('ticket-note'));
      row.append(view);
    }
    return row;
  }));
};
$('publish').onclick = async () => {
  const channel = $('destination').value;
  if (!channel) { $('embed-note').textContent = 'Load channels and choose a destination.'; return; }
  const destinationLabel = $('destination').selectedOptions[0].textContent;
  const message = previewRequest();
  $('publish').disabled = true;
  try {
    const draft = await preview(false, message);
    if (!draft) return;
    if (/\[\[[A-Z][^\[\]\r\n]*\]\]/.test(JSON.stringify(draft.message))) {
      $('embed-note').textContent = 'Fill in the [[placeholders]] before publishing.';
      return;
    }
    if (!window.confirm(`Post this message to ${destinationLabel}?`)) return;
    const posted = await request('publish', {channel, ...message}, $('embed-note'));
    if (posted) $('embed-note').textContent = `Published. ${posted}`;
  } finally {
    $('publish').disabled = false;
  }
};

(async () => {
  const info = await request('info', {}, $('activity'));
  if (!info) return;
  $('product').textContent = info.name;
  document.title = info.name;
  personalEdition = info.personal;
  if (personalEdition) {
    document.body.classList.add('personal');
    $('operations-title').textContent = 'Flight operations';
    $('member-heading').textContent = 'Arrivals and departures';
  }
  if (info.branding) {
    $('branding').hidden = false;
    showBranding(info.branding);
  }
  templates = info.templates;
  $('templates').replaceChildren(...templates.map(template => new Option(template.name)));
  showStatus(info.status);
  const settings = await loadSettings();
  if (settings && !settings.hasToken) navigate('settings');
})();
