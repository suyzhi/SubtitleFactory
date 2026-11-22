// One stroke weight and grid for every control icon, so player and top bar read as a single set.
// The settings gear follows Lucide (ISC).
const PATHS = {
  play: <path d="M7 4.8v14.4a1 1 0 0 0 1.5.86l12-7.2a1 1 0 0 0 0-1.72l-12-7.2A1 1 0 0 0 7 4.8z" fill="currentColor" stroke="none"/>,
  pause: <><rect x="6" y="4.5" width="4.2" height="15" rx="1.3" fill="currentColor" stroke="none"/><rect x="13.8" y="4.5" width="4.2" height="15" rx="1.3" fill="currentColor" stroke="none"/></>,
  replay: <><path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3"/><path d="M4.5 4.5v3.8h3.8"/></>,
  frameBack: <><path d="M6 5v14"/><path d="M18.5 6.2 10 12l8.5 5.8z" fill="currentColor"/></>,
  frameForward: <><path d="M18 5v14"/><path d="M5.5 6.2 14 12l-8.5 5.8z" fill="currentColor"/></>,
  loop: <><path d="M17 3.5 20 6.5l-3 3"/><path d="M4 11.5V10a3.5 3.5 0 0 1 3.5-3.5H20"/><path d="M7 20.5 4 17.5l3-3"/><path d="M20 12.5V14a3.5 3.5 0 0 1-3.5 3.5H4"/></>,
  volume: <><path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5z" fill="currentColor"/><path d="M15.5 9a4 4 0 0 1 0 6"/><path d="M18 6.5a7.5 7.5 0 0 1 0 11"/></>,
  muted: <><path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5z" fill="currentColor"/><path d="m16 9.5 5 5"/><path d="m21 9.5-5 5"/></>,
  captions: <><rect x="3" y="5.5" width="18" height="13" rx="3"/><path d="M10.5 10.2a2.3 2.3 0 1 0 0 3.6"/><path d="M17 10.2a2.3 2.3 0 1 0 0 3.6"/></>,
  theater: <><rect x="3" y="6" width="18" height="12" rx="2.5"/><path d="M3 14.5h18"/></>,
  fullscreen: <><path d="M4 9V5.5A1.5 1.5 0 0 1 5.5 4H9"/><path d="M15 4h3.5A1.5 1.5 0 0 1 20 5.5V9"/><path d="M20 15v3.5a1.5 1.5 0 0 1-1.5 1.5H15"/><path d="M9 20H5.5A1.5 1.5 0 0 1 4 18.5V15"/></>,
  exitFullscreen: <><path d="M9 4v3.5A1.5 1.5 0 0 1 7.5 9H4"/><path d="M20 9h-3.5A1.5 1.5 0 0 1 15 7.5V4"/><path d="M15 20v-3.5a1.5 1.5 0 0 1 1.5-1.5H20"/><path d="M4 15h3.5A1.5 1.5 0 0 1 9 16.5V20"/></>,
  settings: <><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></>,
  plus: <path d="M12 5v14M5 12h14"/>,
  link: <><path d="M10 13.5a4.5 4.5 0 0 0 6.4.3l2.8-2.8a4.5 4.5 0 0 0-6.4-6.4l-1.2 1.2"/><path d="M14 10.5a4.5 4.5 0 0 0-6.4-.3l-2.8 2.8a4.5 4.5 0 0 0 6.4 6.4l1.2-1.2"/></>,
  sun: <><circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4 6 18M18 6l1.4-1.4"/></>,
  moon: <path d="M19.5 14.2A7.8 7.8 0 0 1 9.8 4.5a7.8 7.8 0 1 0 9.7 9.7z"/>,
} as const;

export type PlayerIconName = keyof typeof PATHS;

export default function PlayerIcon({ name }: { name: PlayerIconName }) {
  return <svg className="control-icon" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"
    fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{PATHS[name]}</svg>;
}
