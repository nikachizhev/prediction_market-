// Lock and chat title on a market of a closed chat; opens the chat (docs/mockups/Main.dc.html).
import { Link } from 'react-router-dom';
import type { ChatRef } from '../api';

export function LockIcon({ size = 12, strokeWidth = 2.2 }: { size?: number; strokeWidth?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" aria-hidden="true">
      <rect x="5" y="11" width="14" height="9" rx="2" />
      <path d="M8 11V8a4 4 0 0 1 8 0v3" />
    </svg>
  );
}

export function ChatTag({ chat, className = '' }: { chat: ChatRef; className?: string }) {
  return (
    <Link to={`/chat/${encodeURIComponent(chat.code)}`} className={`chat-tag ${className}`}>
      <LockIcon />
      <span className="chat-tag-title">{chat.title}</span>
    </Link>
  );
}
