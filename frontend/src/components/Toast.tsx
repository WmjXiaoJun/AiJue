import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";
import { IconAlert, IconCheck, IconInfo } from "./icons";

export type ToastTone = "success" | "error" | "info";

interface ToastItem {
  id: number;
  message: string;
  tone: ToastTone;
}

const ToastContext = createContext<(message: string, tone?: ToastTone) => void>(() => undefined);

export function useToast() {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const seq = useRef(0);

  const notify = useCallback((message: string, tone: ToastTone = "info") => {
    seq.current += 1;
    const id = seq.current;
    setItems((list) => [...list.slice(-3), { id, message, tone }]);
    window.setTimeout(() => {
      setItems((list) => list.filter((item) => item.id !== id));
    }, 4600);
  }, []);

  return (
    <ToastContext.Provider value={notify}>
      {children}
      <div className="cm-toast-stack" role="status" aria-live="polite">
        {items.map((item) => (
          <div key={item.id} className={"cm-toast is-" + item.tone}>
            {item.tone === "success" ? <IconCheck size={16} /> : item.tone === "error" ? <IconAlert size={16} /> : <IconInfo size={16} />}
            <span>{item.message}</span>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
