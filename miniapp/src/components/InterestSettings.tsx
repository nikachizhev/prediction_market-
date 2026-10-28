import { useEffect, useState } from 'react';
import { api, ApiError, type Category } from '../api';
import { useSession } from '../session';
import { useToast } from './Toast';

export function InterestSettings() {
  const { me, patchMe } = useSession();
  const toast = useToast();
  const [categories, setCategories] = useState<Category[]>([]);
  const [selected, setSelected] = useState<string[] | null>(null);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const [saving, setSaving] = useState(false);
  const [restoring, setRestoring] = useState(false);
  useEffect(() => {
    let cancelled = false;
    setError('');
    api.categories().then((rows) => { if (!cancelled) setCategories(rows); }).catch(() => { if (!cancelled) setError('Не удалось загрузить темы.'); });
    return () => { cancelled = true; };
  }, [attempt]);
  if (!me) return null;
  const choices = selected ?? me.interests;
  const save = async () => {
    if (saving) return;
    setSaving(true);
    try {
      const result = await api.updateMe({ interests: categories.filter((c) => choices.includes(c.slug)).map((c) => c.slug) });
      patchMe((prev) => ({ ...prev, interests: result.interests }));
      setSelected(null);
      toast('Интересы сохранены');
    } catch (err) { toast(err instanceof ApiError ? err.message : 'Не удалось сохранить интересы.', 'error'); }
    finally { setSaving(false); }
  };
  const restore = async () => {
    if (restoring) return;
    setRestoring(true);
    try { const result = await api.resetHiddenMarkets(); toast(result.restored ? 'Отметки «Неинтересно» сброшены. Рынки снова появятся в ленте' : 'Нет рынков с отметкой «Неинтересно»'); }
    catch (err) { toast(err instanceof ApiError ? err.message : 'Не удалось вернуть рынки.', 'error'); }
    finally { setRestoring(false); }
  };
  return <section className="card interest-settings" aria-label="Интересы и рекомендации">
    <h2 className="profile-section-title">Твои интересы</h2>
    <p className="sub small">Выбери темы для ленты «Для тебя». Остальные категории останутся доступны. Без выбора покажем свежие и популярные рынки.</p>
    {error ? <p role="alert">{error} <button type="button" onClick={() => setAttempt((value) => value + 1)}>Повторить</button></p> :
      <div className="interest-chips">{categories.map((c) => <button key={c.slug} type="button" className={`chip ${choices.includes(c.slug) ? 'is-active' : ''}`} aria-pressed={choices.includes(c.slug)} disabled={saving} onClick={() => setSelected(choices.includes(c.slug) ? choices.filter((slug) => slug !== c.slug) : [...choices, c.slug])}>{c.label}</button>)}</div>}
    <button type="button" className="button-secondary" disabled={saving || !categories.length || selected === null} onClick={() => void save()}>{saving ? 'Сохраняем…' : 'Сохранить интересы'}</button>
    <p className="sub small" id="reset-uninterested-description">Вернёт в ленту все рынки, на которых ты нажал «Неинтересно».</p>
    <button type="button" className="button-secondary restore-hidden" aria-describedby="reset-uninterested-description" disabled={restoring} onClick={() => void restore()}>{restoring ? 'Сбрасываем…' : 'Сбросить отметки «Неинтересно»'}</button>
  </section>;
}
