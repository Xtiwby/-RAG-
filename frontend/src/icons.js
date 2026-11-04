import { h } from 'vue'
const icon = paths => (props = {}) => h('svg', {
  width: props.size || 20, height: props.size || 20, viewBox: '0 0 24 24',
  fill: 'none', stroke: 'currentColor', 'stroke-width': 1.7,
  'stroke-linecap': 'round', 'stroke-linejoin': 'round',
  class: ['ui-icon', props.class], 'aria-hidden': 'true', focusable: 'false',
}, paths.map(d => h('path', { d })))
export const Activity = icon(['M3 12h4l3-8 4 16 3-8h4'])
export const BookOpen = icon(['M12 5v16M3 4c4-1 6 0 9 2 3-2 5-3 9-2v15c-4-1-6 0-9 2-3-2-5-3-9-2Z'])
export const Bot = icon(['M8 3h8l5 5v8l-5 5H8l-5-5V8Z','M8 9h.01M16 9h.01M8 14h8M12 3v4'])
export const ChevronLeft = icon(['m14 6-6 6 6 6'])
export const ChevronDown = icon(['m6 9 6 6 6-6'])
export const Database = icon(['M3 6c0-4 18-4 18 0s-18 4-18 0v12c0 4 18 4 18 0V6','M3 12c0 4 18 4 18 0'])
export const FileSearch = icon(['M13 2H5v20h14V8Z','M13 2v6h6','M10 11a3 3 0 1 0 0 6 3 3 0 0 0 0-6m2 5 3 3'])
export const FileText = icon(['M13 2H5v20h14V8Z','M13 2v6h6M8 12h8M8 16h6'])
export const LayoutDashboard = icon(['M3 3h7v9H3ZM14 3h7v5h-7ZM3 16h7v5H3ZM14 12h7v9h-7Z'])
export const LogOut = icon(['M9 3H4v18h5M13 8l5 4-5 4M8 12h13'])
export const Menu = icon(['M4 6h16M4 12h16M4 18h16'])
export const Search = icon(['M10.5 3a7.5 7.5 0 1 0 0 15 7.5 7.5 0 0 0 0-15m5.5 13 5 5'])
export const ShieldCheck = icon(['M12 2 3 6v6c0 5 9 10 9 10s9-5 9-10V6Z','m8 12 3 3 5-6'])
export const Users = icon(['M8 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8M2 21v-4c0-5 12-5 12 0v4M16 3c5 0 5 8 0 8M18 14c3 0 4 2 4 4v3'])
export const X = icon(['m6 6 12 12M18 6 6 18'])
export const Image = icon(['M3 3h18v18H3Z','m3 16 6-6 12 11M15 6h.01'])
export const LayoutTemplate = icon(['M3 3h18v18H3ZM3 9h18M10 9v12'])
export const Plus = icon(['M12 4v16M4 12h16'])
export const Trash2 = icon(['M3 6h18M8 6V3h8v3M5 6l1 15h12l1-15M10 10v7M14 10v7'])
export const Table2 = icon(['M3 3h18v18H3ZM3 9h18M3 15h18M9 3v18M15 3v18'])
export const FileUp = icon(['M13 2H5v20h14V8ZM13 2v6h6','m9 14 3-3 3 3M12 11v7'])
export const SlidersHorizontal = icon(['M3 6h5M12 6h9M3 12h11M18 12h3M3 18h2M9 18h12M8 3v6M14 9v6M5 15v6'])
export const Send = icon(['m3 3 19 9-19 9 4-9Z','M7 12h15'])
export const User = icon(['M12 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8M4 21v-3c0-6 16-6 16 0v3'])
export const FilePlus2 = icon(['M13 2H5v20h14V8ZM13 2v6h6M9 14h6M12 11v6'])
export const Play = icon(['m8 4 12 8-12 8Z'])
export const RefreshCw = icon(['M20 7V2m0 5h-5M4 17v5m0-5h5','M20 7a9 9 0 0 0-16 1M4 17a9 9 0 0 0 16-1'])
export const Settings = icon(['M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8','m9 2-1 3-3 1-3 3 2 3-2 3 3 3 3 1 1 3h6l1-3 3-1 3-3-2-3 2-3-3-3-3-1-1-3Z'])
export const ArrowRight = icon(['M4 12h16m-6-6 6 6-6 6'])
export const MoreHorizontal = icon(['M5 12h.01M12 12h.01M19 12h.01'])

