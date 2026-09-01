import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const cases = JSON.parse(readFileSync(0, 'utf8'));

function detect({ body = '', title = '', url = 'https://www.zhipin.com/web/geek/chat', topSelector, script }) {
    const dom = new JSDOM(`<!doctype html><title>${title}</title><body>${body}</body>`, {
        runScripts: 'outside-only',
        url,
    });
    const { document, HTMLElement } = dom.window;
    Object.defineProperty(HTMLElement.prototype, 'getBoundingClientRect', {
        configurable: true,
        value() {
            return { left: 10, top: 10, width: 100, height: 30, right: 110, bottom: 40 };
        },
    });
    document.elementFromPoint = () => document.querySelector(topSelector || '[data-top]') || document.body;
    return JSON.parse(dom.window.eval(script));
}

process.stdout.write(JSON.stringify(cases.map(detect)));
