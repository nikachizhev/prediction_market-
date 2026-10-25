import { useState } from 'react';
import { initials } from '../rating';
import './avatar.css';

interface AvatarProps {
  name: string;
  photo: string | null;
  size?: number;
  /** Text instead of initials, e.g. «Ты». */
  label?: string;
}

/** Round avatar: Telegram photo, or initials when there is none (or it fails to load). */
export function Avatar({ name, photo, size = 36, label }: AvatarProps) {
  const [broken, setBroken] = useState(false);
  const style = { width: size, height: size };
  if (photo && !broken) {
    return <img className="avatar" src={photo} alt="" style={style} onError={() => setBroken(true)} />;
  }
  return (
    <span className="avatar" style={{ ...style, fontSize: Math.round(size * 0.36) }} aria-hidden="true">
      {label ?? initials(name)}
    </span>
  );
}
