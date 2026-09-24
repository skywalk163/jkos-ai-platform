/**
 * 极快AI操作系统（JKOS）harness 插件 —— Client 半包（浏览器侧）。
 *
 * 手写经典脚本（无 ESM 语法、无构建）：匹配产物格式
 * `window.__ModuleLoader__.load({ id, factory(require) })`，`id` 必须等于包名。
 * `require` 只能取平台模块表里的词，这里只用 `react`。
 *
 * 贡献两处 UI：侧栏面板入口（`sidebar.panellist`，list）与主区面板
 * （`main`，keyed）。两者共用同一个 id —— 侧栏按钮的 id 就是主区 keyed 分派
 * 的 key，点击即切到本面板。面板内容是同源 iframe，指向 Host 半包的
 * `/jkos/` 反代路径。
 */
window.__ModuleLoader__.load({
  id: 'jkos-plugin',
  factory(require) {
    const React = require('react')
    const h = React.createElement

    /** 侧栏入口 id，同时是主区 keyed slot 的 key。 */
    const PANEL_ID = 'jkos'
    /** 侧栏显示名（与 Host 半包的反代路径无关，可自由改）。 */
    const PANEL_LABEL = '极快AI操作系统'
    /** iframe 指向的反代路径，须与 Host 半包的 ROUTE_PREFIX 一致。 */
    const FRAME_SRC = '/jkos/'

    const style = document.createElement('style')
    style.dataset.plugin = 'jkos-plugin'
    style.textContent = [
      '.jkos-panel { flex: 1; min-height: 0; display: flex; }',
      '.jkos-frame { flex: 1; border: 0; display: block; background: #fff; }',
      '.jkos-icon { display: block; color: inherit; }',
    ].join('\n')
    document.head.append(style)

    /** 主区面板：同源 iframe 承载 JKOS 自己的页面。 */
    function JkosPanel() {
      return h('div', { className: 'jkos-panel' },
        h('iframe', {
          className: 'jkos-frame',
          src: FRAME_SRC,
          title: PANEL_LABEL,
          referrerPolicy: 'no-referrer',
        }))
    }

    /** 侧栏入口图标：由侧栏提供尺寸与选中态。 */
    function JkosPanelIcon(props) {
      return h('svg', {
        className: 'jkos-icon',
        width: props.size,
        height: props.size,
        viewBox: '0 0 24 24',
        'aria-hidden': true,
        style: { opacity: props.active ? 1 : 0.65 },
      },
      h('rect', {
        x: 2.5, y: 2.5, width: 19, height: 19, rx: 5,
        fill: 'none', stroke: 'currentColor', strokeWidth: 1.6,
      }),
      h('text', {
        x: 12, y: 16.6, textAnchor: 'middle',
        fontSize: 11, fill: 'currentColor',
      }, '极'))
    }

    return {
      inject: ['slots'],
      apply(ctx) {
        ctx.slots.inject('main', () => ctx.slots.register({
          name: 'main',
          key: PANEL_ID,
        }, JkosPanel))
        ctx.slots.inject('sidebar.panellist', () => ctx.slots.register({
          name: 'sidebar.panellist',
          id: PANEL_ID,
          order: 10,
          label: PANEL_LABEL,
        }, JkosPanelIcon))
      },
    }
  },
})