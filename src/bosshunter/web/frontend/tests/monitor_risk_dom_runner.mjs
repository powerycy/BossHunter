import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const cases = JSON.parse(readFileSync(0, 'utf8'));

function detect({ body = '', title = '', url = 'https://www.zhipin.com/web/geek/chat', topSelector, rects = {}, script }) {
    const dom = new JSDOM(`<!doctype html><title>${title}</title><body>${body}</body>`, {
        runScripts: 'outside-only',
        url,
    });
    const { document, HTMLElement } = dom.window;
    document.title = title;
    const defaultRect = { left: 10, top: 10, width: 100, height: 30 };
    const rectangleFor = (element) => {
        for (const [selector, rect] of Object.entries(rects)) {
            if (element.matches(selector)) return { ...defaultRect, ...rect };
        }
        return defaultRect;
    };
    const toDomRect = (rect) => ({
        ...rect,
        right: rect.left + rect.width,
        bottom: rect.top + rect.height,
        x: rect.left,
        y: rect.top,
    });
    Object.defineProperty(HTMLElement.prototype, 'getBoundingClientRect', {
        configurable: true,
        value() {
            return toDomRect(rectangleFor(this));
        },
    });
    dom.window.Range.prototype.getClientRects = function getClientRects() {
        const element = this.startContainer.nodeType === 3
            ? this.startContainer.parentElement
            : this.commonAncestorContainer.parentElement || this.commonAncestorContainer;
        return [element]
            .filter((element) => element instanceof HTMLElement)
            .map((element) => toDomRect(rectangleFor(element)));
    };
    document.elementFromPoint = () => document.querySelector(topSelector || '[data-top]') || document.body;
    return JSON.parse(dom.window.eval(script));
}

process.stdout.write(JSON.stringify(cases.map(detect)));
