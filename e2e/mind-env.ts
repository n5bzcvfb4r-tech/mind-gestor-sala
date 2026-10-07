import type { Page } from '@playwright/test';

/**
 * Bypass MAPFRE-style "Select an environment" modal (dev / pre / pro).
 * Used by welcome seeds and platform-generated canonical specs.
 * Override with MIND_E2E_ENV (default: dev).
 */
export async function ensureEnvironment(page: Page): Promise<void> {
  const env = process.env.MIND_E2E_ENV || 'dev';
  const candidates = [
    page.getByText(env, { exact: true }),
    page.getByRole('link', { name: new RegExp(`^${env}$`, 'i') }),
    page.getByRole('button', { name: new RegExp(`^${env}$`, 'i') }),
  ];
  for (const pick of candidates) {
    try {
      if (await pick.first().isVisible({ timeout: 1_500 })) {
        await pick.first().click();
        await page.waitForLoadState('domcontentloaded').catch(() => undefined);
        return;
      }
    } catch {
      /* try next */
    }
  }
}
