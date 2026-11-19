/**
 * 跳转定位 + 目标高亮。
 *
 * 背景：很多操作的结果渲染在页面另一处（例如「生成章节」的结果在下方、「创建令牌」
 * 的明文只出现一次），用户点完不知道去哪看。统一用这套工具：滚到目标 + 高亮脉冲。
 *
 * 高亮样式定义在 `app/globals.css` 的 `.jump-highlight`（outline 脉冲，不影响布局，
 * 且尊重 prefers-reduced-motion）。
 */

export const HIGHLIGHT_CLASS = "jump-highlight";
const HIGHLIGHT_MS = 2200;
const MAX_WAIT_FRAMES = 90; // ≈1.5s @60fps

/** 给元素加一次高亮脉冲；重复调用会重启动画。 */
export function flashTarget(target: HTMLElement): void {
  target.classList.remove(HIGHLIGHT_CLASS);
  // 读一次布局属性强制重排，保证同一元素上能重新播放动画。
  void target.offsetWidth;
  target.classList.add(HIGHLIGHT_CLASS);
  window.setTimeout(() => target.classList.remove(HIGHLIGHT_CLASS), HIGHLIGHT_MS);
}

/**
 * 等待锚点渲染出来后执行回调。
 *
 * 结果区常在状态更新后的下一帧（甚至数据请求返回后）才进 DOM，因此用有上限的
 * rAF 轮询，找到即停，避免无限循环。
 */
export function locateAnchor(
  anchorId: string,
  onFound: (element: HTMLElement) => void,
  maxFrames: number = MAX_WAIT_FRAMES,
): void {
  if (typeof window === "undefined") return;
  let frames = 0;
  const tick = () => {
    const element = document.getElementById(anchorId);
    if (element) {
      onFound(element);
      return;
    }
    frames += 1;
    if (frames < maxFrames) window.requestAnimationFrame(tick);
  };
  window.requestAnimationFrame(tick);
}

/** 滚到锚点 + 高亮 + 可选聚焦。等不到锚点就静默放弃（不报错、不打断用户）。 */
export function jumpToAnchor(anchorId: string, options?: { focus?: boolean }): void {
  locateAnchor(anchorId, (target) => {
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    flashTarget(target);
    if (options?.focus !== false) {
      const focusable = target.querySelector<HTMLElement>(
        'input:not([type="hidden"]), select, textarea, button',
      );
      focusable?.focus({ preventScroll: true });
    }
  });
}
