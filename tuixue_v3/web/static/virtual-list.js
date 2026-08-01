// ────────────────────────────────────────────
// VirtualList — fixed-height 虚拟滚动 (Sprint 2, 2026-08-01)
// 启发式: react-window 的 FixedSizeList — 仅渲染视口可见 + 上下 5 行 sentinel,
// 14k 行变 30 行 DOM 节点,innerHTML 插入成本 16MB → 80KB (200x 降)
// ────────────────────────────────────────────
class VirtualList {
  /**
   * @param {HTMLElement} container  滚动容器 (let overflow:auto)
   * @param {Object} opts
   *   @prop {Function} renderRow - (item, idx) => string  HTML for one row
   *   @prop {number} rowHeight   - 固定行高 (px)
   *   @prop {number} overscan    - 上下预渲染行数 (默认 5)
   *   @prop {string} tag         - 行标签 (默认 'div')
   *   @prop {string} rowClass    - 行 class
   *   @prop {string} viewportTag - viewport 标签 (默认 'div')
   */
  constructor(container, opts) {
    this.container = container;
    this.renderRow = opts.renderRow;
    this.rowHeight = opts.rowHeight || 36;
    this.overscan = opts.overscan || 5;
    this.tag = opts.tag || 'div';
    this.rowClass = opts.rowClass || 'vl-row';
    this.vpTag = opts.viewportTag || 'div';
    this.items = [];
    this._scrollHandler = null;
    this._rafPending = false;
    this._lastRange = [-1, -1];
    this._build();
  }

  _build() {
    // container 设定位 context
    if (getComputedStyle(this.container).position === 'static') {
      this.container.style.position = 'relative';
    }
    this.container.style.overflow = 'auto';
    // spacer 撑出全列表高度 (scroll bar 反映总长度)
    this.spacer = document.createElement('div');
    this.spacer.style.cssText = 'width:100%;height:0;pointer-events:none;';
    this.container.appendChild(this.spacer);
    // viewport 装渲染行 (absolute 定位)
    this.viewport = document.createElement(this.vpTag);
    this.viewport.style.cssText = 'position:absolute;top:0;left:0;right:0;will-change:transform;';
    this.container.appendChild(this.viewport);
    // rAF 节流 scroll
    this._scrollHandler = () => {
      if (this._rafPending) return;
      this._rafPending = true;
      requestAnimationFrame(() => {
        this._rafPending = false;
        this._render();
      });
    };
    this.container.addEventListener('scroll', this._scrollHandler, { passive: true });
  }

  /** 替换列表全部数据 */
  setItems(items) {
    this.items = items || [];
    this.spacer.style.height = (this.items.length * this.rowHeight) + 'px';
    this._lastRange = [-1, -1];
    this._render();
  }

  /** 当前可视起止 index (含 overscan) */
  _range() {
    const top = this.container.scrollTop;
    const h = this.container.clientHeight;
    const start = Math.max(0, Math.floor(top / this.rowHeight) - this.overscan);
    const end = Math.min(
      this.items.length,
      Math.ceil((top + h) / this.rowHeight) + this.overscan
    );
    return [start, end];
  }

  _render() {
    const [start, end] = this._range();
    if (start === this._lastRange[0] && end === this._lastRange[1]) return;
    this._lastRange = [start, end];
    const slice = this.items.slice(start, end);
    const tpl = slice.map((it, i) => this.renderRow(it, start + i)).join('');
    this.viewport.style.transform = `translateY(${start * this.rowHeight}px)`;
    if (this.viewport.innerHTML !== tpl) {
      this.viewport.innerHTML = tpl;
    }
  }

  /** 滚到指定 index */
  scrollToIndex(idx) {
    this.container.scrollTop = idx * this.rowHeight;
    this._render();
  }

  /** 销毁 */
  destroy() {
    if (this._scrollHandler) {
      this.container.removeEventListener('scroll', this._scrollHandler);
      this._scrollHandler = null;
    }
    if (this.spacer && this.spacer.parentNode) this.spacer.parentNode.removeChild(this.spacer);
    if (this.viewport && this.viewport.parentNode) this.viewport.parentNode.removeChild(this.viewport);
    this.items = [];
  }
}

window.VirtualList = VirtualList;
