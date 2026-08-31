/* One bounded route from the Extella panel to explicitly allowed experts. */
const hasOwn = (value, key) => Boolean(value)
  && typeof value === 'object'
  && Object.prototype.hasOwnProperty.call(value, key);

function transportPayload(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return { found: false };
  /* Extella Desktop currently uses `res`; older hosts use `result`. */
  const hasRes = hasOwn(value, 'res');
  if (hasRes && value.res !== undefined && value.res !== null) {
    return { found: true, value: value.res };
  }
  if (!hasOwn(value, 'result')) return hasRes ? { found: true, value: value.res } : { found: false };
  const marked = value.status === 'ok'
    || value.type === 'etb_expert_result'
    || value.ok === true
    || hasOwn(value, 'expert_name')
    || hasOwn(value, 'agent_id')
    || hasOwn(value, 'reqId');
  const serializedResult = typeof value.result === 'string' && /^\s*[\[{]/.test(value.result);
  if (marked || hasRes || serializedResult) return { found: true, value: value.result };
  return { found: false };
}

function unwrapExtellaResult(input) {
  let value = input;
  for (let depth = 0; depth < 10; depth += 1) {
    if (typeof value === 'string') {
      try {
        value = JSON.parse(value);
      } catch {
        throw new Error('Эксперт вернул ответ, который нельзя прочитать.');
      }
      continue;
    }

    const payload = transportPayload(value);
    if (!payload.found) return value;
    value = payload.value;
  }
  throw new Error('Ответ Extella содержит слишком много транспортных обёрток.');
}

class ExtellaBridge {
  constructor({ timeoutMs = 240000, allowedExperts = [] } = {}) {
    this.timeoutMs = timeoutMs;
    this.allowedExperts = new Set(allowedExperts);
    this.pending = new Map();
    this.hostListeners = new Set();
    this.device = null;
    this.deviceReady = new Promise(resolve => { this.resolveDevice = resolve; });
    window.addEventListener('message', event => this.onMessage(event));
  }

  get embedded() { return window.parent !== window; }

  id() {
    const random = crypto.getRandomValues(new Uint32Array(1))[0].toString(36);
    return `seo-panel-${Date.now()}-${random}`;
  }

  subscribeHost(listener) {
    this.hostListeners.add(listener);
    return () => this.hostListeners.delete(listener);
  }

  async run(expert, params = {}, { timeoutMs = this.timeoutMs } = {}) {
    if (!this.allowedExperts.has(expert)) {
      return Promise.resolve({ ok: false, error: 'Этот маршрут не разрешён панели.' });
    }
    if (!this.embedded) {
      return Promise.resolve({ ok: false, error: 'Открой панель внутри Extella и повтори.' });
    }
    const target = this.device || await Promise.race([
      this.deviceReady,
      new Promise(resolve => window.setTimeout(() => resolve(null), 5000)),
    ]);
    if (!target) {
      return { ok: false, error: 'Extella не передала привязанное устройство.' };
    }
    const reqId = this.id();
    return new Promise(resolve => {
      let timer;
      const settle = value => {
        const pending = this.pending.get(reqId);
        if (!pending) return;
        this.pending.delete(reqId);
        window.clearTimeout(pending.timer);
        resolve(value);
      };
      timer = window.setTimeout(() => {
        settle({ ok: false, error: 'Extella не подтвердила выполнение за отведённое время.' });
      }, timeoutMs);
      this.pending.set(reqId, { resolve: settle, timer });
      try {
        window.parent.postMessage({type:'etb_run_expert', reqId, name: expert, params, target}, '*');
      } catch {
        settle({ ok: false, error: 'Extella не приняла запрос эксперта.' });
      }
    });
  }

  onMessage(event) {
    if (event.source !== window.parent) return;
    const data = event.data || {};
    if (data.type === 'etb_init' || data.type === 'etb_theme') {
      if (data.type === 'etb_init') {
        this.device = data.device || null;
        if (this.device) this.resolveDevice(this.device);
      }
      this.hostListeners.forEach(listener => listener(data));
      return;
    }
    if (data.type !== 'etb_expert_result' || !this.pending.has(data.reqId)) return;
    const pending = this.pending.get(data.reqId);

    if (data.ok === false) {
      pending.resolve({
        ok: false,
        error: data.error || data.message || 'Эксперт не выполнил запрос.',
      });
      return;
    }
    try {
      const value = unwrapExtellaResult(data);
      if (value && typeof value === 'object' && value.status === 'error') {
        pending.resolve({ ok: false, error: value.message || 'Эксперт не выполнил запрос.' });
        return;
      }
      pending.resolve({ ok: true, data: value });
    } catch (error) {
      pending.resolve({ ok: false, error: error.message || 'Ответ эксперта нельзя прочитать.' });
    }
  }
}

if (typeof window !== 'undefined') {
  window.ExtellaBridge = ExtellaBridge;
  window.unwrapExtellaResult = unwrapExtellaResult;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { ExtellaBridge, unwrapExtellaResult };
}
