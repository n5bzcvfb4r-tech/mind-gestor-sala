import { test, expect } from '@playwright/test';
import { ensureEnvironment } from './mind-env';

/**
 * @smoke — app boots and the default route mounts without a fatal page error.
 * Platform runs this when PlaywrightTriggerPolicy selects smoke/functional in-session.
 *
 * Prefer `app-root` attached over `body`/`app-root` visibility: Angular/Ionic often
 * leave those nodes failing Playwright's "visible" heuristic even when the shell mounted.
 */
test.describe('@smoke welcome shell', () => {
  test('home loads without pageerror', async ({ page }) => {
    test.setTimeout(30_000);
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(String(err)));
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await ensureEnvironment(page);
    await expect(page.locator('app-root')).toBeAttached({ timeout: 15_000 });
    expect(errors, `pageerrors: ${errors.join('; ')}`).toEqual([]);
  });
});
