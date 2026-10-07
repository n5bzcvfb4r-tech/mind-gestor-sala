import { test } from '@playwright/test';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { ensureEnvironment } from './mind-env';

/**
 * @visual — ephemeral screenshot only (NO toHaveScreenshot baselines in repo).
 * Post-PR job collects PNGs under test-results / MIND_BROWSER_ARTIFACT_DIR.
 */
test.describe('@visual welcome', () => {
  test('capture home screenshot artifact', async ({ page }) => {
    await page.goto('/');
    await ensureEnvironment(page);
    await page.waitForLoadState('networkidle').catch(() => undefined);
    const outDir =
      process.env.MIND_BROWSER_ARTIFACT_DIR ||
      path.join(process.cwd(), 'test-results', 'visual');
    fs.mkdirSync(outDir, { recursive: true });
    const file = path.join(outDir, 'welcome-home.png');
    await page.screenshot({ path: file, fullPage: true });
  });
});
