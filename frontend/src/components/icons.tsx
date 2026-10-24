import type { ReactNode } from "react";

export interface IconProps {
  size?: number;
  className?: string;
}

function make(children: ReactNode, strokeWidth = 1.7) {
  return function Icon({ size = 18, className }: IconProps) {
    return (
      <svg
        width={size}
        height={size}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
        className={className}
        aria-hidden="true"
        focusable="false"
      >
        {children}
      </svg>
    );
  };
}

/** 品牌标：黄底光圈 */
export function BrandMark({ size = 36 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id="brand-mark-g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#FFD84D" />
          <stop offset="100%" stopColor="#E5B207" />
        </linearGradient>
      </defs>
      <rect width="48" height="48" rx="13" fill="url(#brand-mark-g)" />
      <g stroke="#14161B" strokeWidth="2.6" strokeLinecap="round" fill="none">
        <circle cx="24" cy="24" r="13.5" />
        <path d="M24 10.5v5.5M37.5 24H32M24 37.5V32M10.5 24H16" />
        <path d="M33.5 14.5l-3.9 3.9M33.5 33.5l-3.9-3.9M14.5 33.5l3.9-3.9M14.5 14.5l3.9 3.9" />
      </g>
      <circle cx="24" cy="24" r="4.2" fill="#14161B" />
    </svg>
  );
}

export const IconProjects = make(
  <>
    <rect x="3" y="3" width="7.5" height="7.5" rx="1.6" />
    <rect x="13.5" y="3" width="7.5" height="7.5" rx="1.6" />
    <rect x="3" y="13.5" width="7.5" height="7.5" rx="1.6" />
    <rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.6" />
  </>
);

export const IconTemplate = make(
  <>
    <path d="M12 3l8.5 4.7L12 12.4 3.5 7.7 12 3z" />
    <path d="M3.5 12.2L12 16.9l8.5-4.7" />
    <path d="M3.5 16.6L12 21.3l8.5-4.7" />
  </>
);

export const IconSettings = make(
  <>
    <circle cx="12" cy="12" r="3.1" />
    <path d="M19.4 14.4a1.7 1.7 0 00.34 1.86l.06.06a2 2 0 11-2.84 2.84l-.06-.06a1.7 1.7 0 00-1.86-.34 1.7 1.7 0 00-1 1.55V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1.1-1.55 1.7 1.7 0 00-1.87.34l-.06.06a2 2 0 11-2.83-2.84l.06-.06a1.7 1.7 0 00.33-1.86 1.7 1.7 0 00-1.55-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.55-1.1 1.7 1.7 0 00-.34-1.87l-.06-.06a2 2 0 112.83-2.83l.06.06a1.7 1.7 0 001.87.33H10a1.7 1.7 0 001-1.55V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.55 1.7 1.7 0 001.86-.33l.06-.06a2 2 0 112.84 2.83l-.06.06a1.7 1.7 0 00-.34 1.87V10a1.7 1.7 0 001.55 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.55 1z" />
  </>
);

export const IconJobs = make(
  <>
    <path d="M4 5h7M4 12h16M4 19h10" />
    <circle cx="18" cy="5" r="2" />
    <circle cx="17" cy="19" r="2" />
  </>
);

export const IconWallet = make(
  <>
    <path d="M3 8.5A2.5 2.5 0 015.5 6H18a3 3 0 013 3v9a2 2 0 01-2 2H5.5A2.5 2.5 0 013 17.5v-9z" />
    <path d="M3 9h18" />
    <circle cx="16.5" cy="14" r="1.2" fill="currentColor" stroke="none" />
  </>
);

export const IconScript = make(
  <>
    <path d="M6 3h9l4 4v14H6z" />
    <path d="M14.5 3v4.5H19" />
    <path d="M9 11h6M9 14.5h6M9 18h3.5" />
  </>
);

export const IconUsers = make(
  <>
    <circle cx="9" cy="8" r="3.2" />
    <path d="M3.5 20a5.5 5.5 0 0111 0" />
    <path d="M16 5.2a3.2 3.2 0 010 5.6M17.4 14.4A5.5 5.5 0 0120.5 20" />
  </>
);

export const IconBoard = make(
  <>
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <path d="M3 10h18M9.5 10v10M15 4v6" />
  </>
);

export const IconTable = make(
  <>
    <rect x="3" y="4.5" width="18" height="15" rx="2" />
    <path d="M3 9.5h18M3 14.5h18M8.5 4.5v15M15 4.5v15" />
  </>
);

export const IconGrid = make(
  <>
    <rect x="3" y="3" width="8" height="8" rx="1.6" />
    <rect x="13" y="3" width="8" height="8" rx="1.6" />
    <rect x="3" y="13" width="8" height="8" rx="1.6" />
    <rect x="13" y="13" width="8" height="8" rx="1.6" />
  </>
);

export const IconSpark = make(
  <>
    <path d="M12 3l1.7 4.9L18.6 9.6l-4.9 1.7L12 16.2l-1.7-4.9L5.4 9.6l4.9-1.7L12 3z" />
    <path d="M18.5 15.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8.8-2.2z" />
  </>
);

export const IconFilm = make(
  <>
    <rect x="2.5" y="4" width="19" height="16" rx="2.4" />
    <path d="M7 4v16M17 4v16M2.5 9h4.5M2.5 15h4.5M17 9h4.5M17 15h4.5" />
  </>
);

export const IconPlay = make(<path d="M8 5.5v13l11-6.5-11-6.5z" />);

export const IconChevronRight = make(<path d="M9 5l7 7-7 7" />);
export const IconChevronLeft = make(<path d="M15 5l-7 7 7 7" />);
export const IconChevronDown = make(<path d="M5 9l7 7 7-7" />);
export const IconClose = make(<path d="M5.5 5.5l13 13M18.5 5.5l-13 13" />);
export const IconCheck = make(<path d="M4.5 12.5l5 5 10-11" />);
export const IconPlus = make(<path d="M12 5v14M5 12h14" />);
export const IconRefresh = make(
  <>
    <path d="M20 11a8 8 0 10-1.7 6.4" />
    <path d="M20 4.5V11h-6.2" />
  </>
);
export const IconLock = make(
  <>
    <rect x="4.5" y="10" width="15" height="10.5" rx="2" />
    <path d="M8 10V7.5a4 4 0 018 0V10" />
  </>
);
export const IconUnlock = make(
  <>
    <rect x="4.5" y="10" width="15" height="10.5" rx="2" />
    <path d="M8 10V7a4 4 0 017.6-1.3" />
  </>
);
export const IconExternal = make(
  <>
    <path d="M14 4h6v6" />
    <path d="M20 4l-8.5 8.5" />
    <path d="M18 14v5a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h5" />
  </>
);
export const IconDownload = make(
  <>
    <path d="M12 3.5v11" />
    <path d="M7.5 10.5L12 15l4.5-4.5" />
    <path d="M4 17.5v1a2 2 0 002 2h12a2 2 0 002-2v-1" />
  </>
);
export const IconAlert = make(
  <>
    <path d="M12 3.5L21 20H3L12 3.5z" />
    <path d="M12 9.5v4.5M12 17.2v.2" />
  </>
);
export const IconInfo = make(
  <>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 11v5.5M12 7.6v.2" />
  </>
);
export const IconCopy = make(
  <>
    <rect x="9" y="9" width="11" height="11" rx="2" />
    <path d="M15 5.5A1.5 1.5 0 0013.5 4H5.5A1.5 1.5 0 004 5.5v8A1.5 1.5 0 005.5 15" />
  </>
);

export const IconTrash = make(
  <>
    <path d="M4 6.5h16" />
    <path d="M9 6.5V4.8A1.3 1.3 0 0110.3 3.5h3.4A1.3 1.3 0 0115 4.8v1.7" />
    <path d="M6 6.5l.8 13a1.5 1.5 0 001.5 1.4h7.4a1.5 1.5 0 001.5-1.4l.8-13" />
    <path d="M10 10.5v6M14 10.5v6" />
  </>
);

export const IconEdit = make(
  <>
    <path d="M4 20h4.2L20 8.2a2 2 0 00-2.8-2.8L5.4 17.2 4 20z" />
    <path d="M13.5 6.5l3.9 3.9" />
  </>
);

export const IconFolder = make(
  <>
    <path d="M3.5 6.5A1.5 1.5 0 015 5h4.2l2 2.5H19a1.5 1.5 0 011.5 1.5V18a1.5 1.5 0 01-1.5 1.5H5A1.5 1.5 0 013.5 18V6.5z" />
  </>
);
export const IconImage = make(
  <>
    <rect x="3" y="4.5" width="18" height="15" rx="2" />
    <circle cx="8.5" cy="9.5" r="1.6" />
    <path d="M21 15l-5-5-7.5 9.5" />
  </>
);
export const IconBox = make(
  <>
    <path d="M12 3l8 4.2v9.6L12 21l-8-4.2V7.2L12 3z" />
    <path d="M4 7.2l8 4.2 8-4.2M12 11.4V21" />
  </>
);
export const IconPalette = make(
  <>
    <path d="M12 3a9 9 0 000 18c1.4 0 2-1 2-2s-.7-2-2-2h-1a2 2 0 01-2-2c0-1.2 1-2 2-2h5a5 5 0 005-5c0-3.3-4-5-9-5z" />
    <circle cx="7.5" cy="11.5" r="1.1" fill="currentColor" stroke="none" />
    <circle cx="10" cy="7.5" r="1.1" fill="currentColor" stroke="none" />
    <circle cx="14.5" cy="7.5" r="1.1" fill="currentColor" stroke="none" />
  </>
);
export const IconUser = make(
  <>
    <circle cx="12" cy="8" r="3.6" />
    <path d="M4.8 20.5a7.2 7.2 0 0114.4 0" />
  </>
);
export const IconClock = make(
  <>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 7.5V12l3 2" />
  </>
);
export const IconVideo = make(
  <>
    <rect x="3" y="6" width="12.5" height="12" rx="2" />
    <path d="M15.5 10.5l5-2.8v8.6l-5-2.8" />
  </>
);
export const IconSearch = make(
  <>
    <circle cx="10.5" cy="10.5" r="6.5" />
    <path d="M15.5 15.5L21 21" />
  </>
);
export const IconSend = make(
  <>
    <path d="M21 3L10.5 13.5" />
    <path d="M21 3l-6.5 18-4-7.5L3 9.5 21 3z" />
  </>
);
export const IconArrowRight = make(
  <>
    <path d="M4 12h15" />
    <path d="M13 6l6 6-6 6" />
  </>
);
export const IconDot = make(<circle cx="12" cy="12" r="3" fill="currentColor" stroke="none" />);
export const IconLayout = make(
  <>
    <rect x="3" y="3" width="18" height="18" rx="2.4" />
    <path d="M3 9h18M9 9v12" />
  </>
);
export const IconVolume = make(
  <>
    <path d="M4 9.5v5h3l4.5 4V5.5L7 9.5H4z" />
    <path d="M15.5 9a4.5 4.5 0 010 6M18 6.5a8 8 0 010 11" />
  </>
);
export const IconSpin = make(<path d="M21 12a9 9 0 11-6.2-8.6" />);
