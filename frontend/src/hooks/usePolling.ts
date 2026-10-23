import { useEffect, useRef } from "react";

/**
 * 定时轮询：enabled 为 false 时暂停；回调串行执行，避免慢请求堆积。
 */
export function usePolling(fn: () => void | Promise<void>, intervalMs: number, enabled = true) {
  const saved = useRef(fn);
  saved.current = fn;
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    let busy = false;
    const tick = async () => {
      if (!alive || busy) return;
      busy = true;
      try {
        await saved.current();
      } catch {
        /* 轮询失败静默：页面保留上一次数据，由顶部状态提示 */
      } finally {
        busy = false;
      }
    };
    const timer = window.setInterval(tick, intervalMs);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [intervalMs, enabled]);
}
