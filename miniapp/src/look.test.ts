import { describe, expect, it } from 'vitest';
import { lookFrom } from './look';
import { sideFills, themes, tokensFor, oracleTheme } from './theme';

describe('the look', () => {
  it('is calm unless the oracle is asked for', () => {
    expect(lookFrom('', undefined)).toBe('calm');
    expect(lookFrom('?demo=1', 'm42')).toBe('calm');
    expect(lookFrom('?look=oracle', undefined)).toBe('oracle');
    expect(lookFrom('', 'look_oracle')).toBe('oracle');
  });

  it('follows Telegram in the calm look and stays dark in the hall', () => {
    expect(tokensFor('calm', 'light')).toBe(themes.light);
    expect(tokensFor('calm', 'dark')).toBe(themes.dark);
    expect(tokensFor('oracle', 'light')).toBe(oracleTheme);
    expect(sideFills('calm')).toEqual({ yes: '#1F6FD6', no: '#C2410C' });
    expect(sideFills('oracle').yes).toBe(oracleTheme.yesFill);
  });
});
