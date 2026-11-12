// A small Markdown renderer for model answers. Build DOM nodes only: raw HTML
// stays text, images never fetch, and links accept explicit HTTP(S) URLs only.
const MARKDOWN_LIMIT = 80000;
const MARKDOWN_DEPTH = 6;

function element(tag, text) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  return item;
}

function safeLink(value) {
  if (!/^https?:\/\//i.test(value) || /[\s\u0000-\u001f\u007f]/.test(value)) return null;
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && url.hostname ? url.href : null;
  } catch { return null; }
}

function inline(target, text, depth = 0) {
  if (depth >= MARKDOWN_DEPTH) { target.append(document.createTextNode(text)); return; }
  let pending = '', position = 0;
  const flush = () => { if (pending) { target.append(document.createTextNode(pending)); pending = ''; } };
  const hasLinks = text.includes('](');
  while (position < text.length) {
    const rest = text.slice(position), char = text[position];
    if (char === '\\' && /[\\`*_[\]{}()#+.!|>~-]/.test(text[position + 1] || '')) {
      pending += text[position + 1]; position += 2; continue;
    }
    if (char === '`') {
      const marker = rest.match(/^`+/)[0], end = text.indexOf(marker, position + marker.length);
      if (end !== -1) {
        flush(); target.append(element('code', text.slice(position + marker.length, end).replace(/\n/g, ' ')));
        position = end + marker.length; continue;
      }
      pending += marker; position += marker.length; continue;
    }
    const image = rest.startsWith('![');
    if (hasLinks && (char === '[' || image)) {
      const start = position + (image ? 2 : 1), labelEnd = text.indexOf('](', start);
      if (labelEnd !== -1) {
        let end = labelEnd + 2, nesting = 1;
        for (; end < text.length && nesting; end++) {
          if (text[end] === '\\') { end++; continue; }
          if (text[end] === '(') nesting++;
          if (text[end] === ')') nesting--;
        }
        if (!nesting) {
          const literal = text.slice(position, end), destination = text.slice(labelEnd + 2, end - 1).trim();
          const href = image ? null : safeLink(destination);
          if (href) {
            flush(); const link = element('a'); link.href = href;
            link.target = '_blank'; link.rel = 'noopener noreferrer';
            inline(link, text.slice(start, labelEnd), depth + 1); target.append(link);
          } else pending += literal;
          position = end; continue;
        }
      }
    }
    const marker = ['**', '__', '~~', '*', '_'].find(value => rest.startsWith(value));
    if (marker && !(marker.includes('_') && /[\p{L}\p{N}]/u.test(text[position - 1] || ''))) {
      const end = text.indexOf(marker, position + marker.length);
      const content = end < 0 ? '' : text.slice(position + marker.length, end);
      if (content.trim()) {
        flush(); const item = element(marker.length === 1 ? 'em' : marker === '~~' ? 'del' : 'strong');
        inline(item, content.trim(), depth + 1); target.append(item); position = end + marker.length; continue;
      }
    }
    if (char === '\n') { flush(); target.append(element('br')); }
    else pending += char;
    position++;
  }
  flush();
}

function cells(line) {
  const result = []; let value = '', ticks = 0;
  for (let index = 0; index < line.length; index++) {
    const char = line[index];
    if (char === '\\' && line[index + 1]) { value += char + line[++index]; continue; }
    if (char === '`') {
      let end = index + 1; while (line[end] === '`') end++;
      const count = end - index; ticks = ticks === count ? 0 : ticks || count;
      value += line.slice(index, end); index = end - 1; continue;
    }
    if (char === '|' && !ticks) { result.push(value.trim()); value = ''; }
    else value += char;
  }
  result.push(value.trim());
  if (line.trimStart().startsWith('|')) result.shift();
  if (line.trimEnd().endsWith('|') && !result.at(-1)) result.pop();
  return result;
}

const fence = line => line.match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
const heading = line => line.match(/^ {0,3}(#{1,6})[ \t]+(.+?)\s*#*\s*$/);
const listItem = line => line.match(/^( *)([-+*]|\d{1,9}[.)])[ \t]+(.*)$/);
const rule = line => /^ {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$/.test(line);
function tableHeader(lines, index) {
  if (!lines[index]?.includes('|') || !lines[index + 1]?.includes('-')) return null;
  const header = cells(lines[index]), separators = cells(lines[index + 1]);
  return header.length === separators.length && separators.every(cell => /^:?-{3,}:?$/.test(cell)) ? {header, separators} : null;
}
function beginsBlock(lines, index) {
  const line = lines[index];
  return fence(line) || heading(line) || listItem(line) || /^ {0,3}>/.test(line) || rule(line) || tableHeader(lines, index);
}

function blocks(target, lines, depth = 0) {
  if (depth >= MARKDOWN_DEPTH) { target.append(element('p', lines.join('\n'))); return; }
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) { index++; continue; }
    const codeFence = fence(line), title = heading(line), item = listItem(line), header = tableHeader(lines, index);
    if (codeFence) {
      const marker = codeFence[1], content = []; index++;
      while (index < lines.length) {
        const closing = lines[index].trim();
        if (closing.length >= marker.length && [...closing].every(char => char === marker[0])) { index++; break; }
        content.push(lines[index++]);
      }
      const pre = element('pre'), code = element('code', content.join('\n'));
      const language = codeFence[2].trim();
      if (/^[a-z0-9_+-]{1,30}$/i.test(language)) code.className = 'language-' + language;
      pre.append(code); target.append(pre); continue;
    }
    if (title) {
      const node = element('h' + Math.min(6, title[1].length + 2)); inline(node, title[2]); target.append(node); index++; continue;
    }
    if (rule(line)) { target.append(element('hr')); index++; continue; }
    if (/^ {0,3}>/.test(line)) {
      const quote = element('blockquote'), content = [];
      while (index < lines.length && /^ {0,3}>/.test(lines[index])) content.push(lines[index++].replace(/^ {0,3}> ?/, ''));
      blocks(quote, content, depth + 1); target.append(quote); continue;
    }
    if (header) {
      const wrapper = element('div'), table = element('table'), head = element('thead'), body = element('tbody');
      wrapper.className = 'table-scroll markdown-table'; wrapper.tabIndex = 0;
      wrapper.setAttribute('role', 'region'); wrapper.setAttribute('aria-label', '回答中的表格，可左右滚动');
      const row = (values, tag) => {
        const tr = element('tr'); values.forEach((value, column) => {
          const cell = element(tag), separator = header.separators[column];
          if (separator.endsWith(':')) cell.setAttribute('data-align', separator.startsWith(':') ? 'center' : 'right');
          inline(cell, value); tr.append(cell);
        }); return tr;
      };
      head.append(row(header.header, 'th')); index += 2;
      while (index < lines.length && lines[index].trim() && lines[index].includes('|')) {
        const values = cells(lines[index]); if (values.length !== header.header.length) break;
        body.append(row(values, 'td')); index++;
      }
      table.append(head, body); wrapper.append(table); target.append(wrapper); continue;
    }
    if (item) {
      const indent = item[1].length, ordered = /^\d/.test(item[2]), list = element(ordered ? 'ol' : 'ul');
      if (ordered && parseInt(item[2], 10) !== 1) list.setAttribute('start', String(parseInt(item[2], 10)));
      while (index < lines.length) {
        const current = listItem(lines[index]);
        if (!current || current[1].length !== indent || /^\d/.test(current[2]) !== ordered) break;
        const content = [current[3]], offset = lines[index].length - current[3].length; index++;
        while (index < lines.length) {
          const next = lines[index], spaces = next.match(/^ */)[0].length;
          if (!next.trim()) {
            let following = index + 1; while (following < lines.length && !lines[following].trim()) following++;
            const sibling = listItem(lines[following] || '');
            if (sibling && sibling[1].length === indent && /^\d/.test(sibling[2]) === ordered) { index = following; break; }
            if (following < lines.length && lines[following].match(/^ */)[0].length > indent) { content.push(''); index++; continue; }
            break;
          }
          if (spaces <= indent) break;
          content.push(next.slice(Math.min(spaces, offset))); index++;
        }
        const li = element('li'); blocks(li, content, depth + 1); list.append(li);
      }
      target.append(list); continue;
    }
    const content = [line]; index++;
    while (index < lines.length && lines[index].trim() && !beginsBlock(lines, index)) content.push(lines[index++]);
    const paragraph = element('p'); inline(paragraph, content.join('\n')); target.append(paragraph);
  }
}

export function renderMarkdown(target, source) {
  const text = String(source ?? '').replace(/\r\n?/g, '\n');
  target.replaceChildren();
  if (!target.className?.split(/\s+/).includes('markdown-body')) target.className = ((target.className || '') + ' markdown-body').trim();
  if (text.length > MARKDOWN_LIMIT) {
    const plain = element('div', text); plain.className = 'markdown-plain'; target.append(plain); return;
  }
  blocks(target, text.split('\n'));
}
