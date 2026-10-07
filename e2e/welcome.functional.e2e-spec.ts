import { test, expect } from '@playwright/test';
import { ensureEnvironment } from './mind-env';

/**
 * @functional — placeholder interaction check for the welcome screen.
 * Agents extend this file (or add sibling *.e2e-spec.ts) per screen_codes / DoD
 * with tags `@smoke` / `@functional` / `@visual` and `@screen:SCR-NNN`.
 */
test.describe('@functional welcome', () => {
  test('welcome page exposes main content', async ({ page }) => {
    await page.goto('/');
    await ensureEnvironment(page);
    await expect(page.locator('body')).toBeVisible();
    const text = (await page.locator('body').innerText()).trim();
    expect(text.length).toBeGreaterThan(0);
  });
});
