/**
 * Responsive layout battery: every supported viewport must render a usable
 * chat — no horizontal overflow, composer fully visible, navigation usable,
 * and (below lg) the documents sheet reachable from the composer paperclip.
 *
 * Prereq: a serving frontend with API behind it (docker stack on :3000, or
 * `npm run dev` with E2E_BASE_URL=http://localhost:5173).
 */

import { expect, test } from '@playwright/test'

const VIEWPORTS: Array<{ name: string; width: number; height: number }> = [
  { name: 'iphone-se', width: 320, height: 568 },
  { name: 'iphone-8', width: 375, height: 667 },
  { name: 'iphone-12-13', width: 390, height: 844 },
  { name: 'pixel-5', width: 412, height: 915 },
  { name: 'ipad-portrait', width: 768, height: 1024 },
  { name: 'ipad-landscape', width: 1024, height: 768 },
  { name: 'laptop-720', width: 1280, height: 720 },
  { name: 'laptop-900', width: 1440, height: 900 },
  { name: 'full-hd', width: 1920, height: 1080 },
]

test.describe('responsive layout', () => {
  for (const { name, width, height } of VIEWPORTS) {
    test(`chat shell usable at ${name} (${width}x${height})`, async ({ page }) => {
      await page.setViewportSize({ width, height })
      await page.goto('/')

      // 1. No horizontal overflow at any viewport.
      const overflow = await page.evaluate(
        () =>
          document.documentElement.scrollWidth -
          document.documentElement.clientWidth,
      )
      expect(overflow, 'horizontal overflow in px').toBeLessThanOrEqual(0)

      // 2. Header is visible and not clipped.
      await expect(page.getByRole('heading', { name: 'Nyaya' })).toBeVisible()

      // 3. Composer fully inside the viewport: textarea and send button
      //    both visible with their bounding boxes inside the window.
      const composer = page.locator('#chat-input')
      await expect(composer).toBeVisible()
      const composerBox = await composer.boundingBox()
      const send = page.getByRole('button', { name: 'Send' })
      await expect(send).toBeVisible()
      const sendBox = await send.boundingBox()
      expect(composerBox).not.toBeNull()
      expect(sendBox).not.toBeNull()
      if (composerBox && sendBox) {
        expect(composerBox.y + composerBox.height).toBeLessThanOrEqual(height)
        expect(sendBox.y + sendBox.height).toBeLessThanOrEqual(height)
        expect(sendBox.x).toBeGreaterThanOrEqual(0)
        expect(sendBox.x + sendBox.width).toBeLessThanOrEqual(width)
      }

      // 4. The message log is the scrolling element, not the page.
      const pageScrolls = await page.evaluate(
        () => document.documentElement.scrollHeight > document.documentElement.clientHeight + 2,
      )
      expect(pageScrolls, 'document itself must not scroll').toBe(false)

      // 5. Example questions are usable (tap-sized, fully visible).
      const example = page
        .getByRole('button')
        .filter({ hasText: 'What is the punishment for murder?' })
        await expect(example).toBeVisible()
      const exampleBox = await example.boundingBox()
      if (exampleBox) {
        expect(exampleBox.x).toBeGreaterThanOrEqual(0)
        expect(exampleBox.x + exampleBox.width).toBeLessThanOrEqual(width)
      }
    })

    test(`documents upload reachable at ${name} (${width}x${height})`, async ({ page }) => {
      await page.setViewportSize({ width, height })
      await page.goto('/')

      if (width < 1024) {
        // Mobile/tablet portrait: paperclip opens the bottom sheet and the
        // upload control is fully visible inside it.
        const paperclip = page.getByRole('button', { name: /documents/i })
        await expect(paperclip).toBeVisible()
        const clipBox = await paperclip.boundingBox()
        if (clipBox) {
          expect(clipBox.x).toBeGreaterThanOrEqual(0)
          expect(clipBox.x + clipBox.width).toBeLessThanOrEqual(width)
          expect(clipBox.y + clipBox.height).toBeLessThanOrEqual(height)
        }
        await paperclip.click()
        const sheet = page.getByRole('dialog', { name: 'Your documents' })
        await expect(sheet).toBeVisible()
        const choose = sheet.getByRole('button', { name: 'Choose a file' })
        await expect(choose).toBeVisible()
        const chooseBox = await choose.boundingBox()
        if (chooseBox) {
          expect(chooseBox.x).toBeGreaterThanOrEqual(0)
          expect(chooseBox.x + chooseBox.width).toBeLessThanOrEqual(width)
          expect(chooseBox.y + chooseBox.height).toBeLessThanOrEqual(height)
        }
        // Escape closes the sheet.
        await page.keyboard.press('Escape')
        await expect(sheet).toBeHidden()
      } else {
        // Desktop: the documents rail is always visible with the upload zone.
        const rail = page.getByRole('complementary', { name: 'Uploaded documents' })
        await expect(rail).toBeVisible()
        await expect(rail.getByRole('button', { name: 'Choose a file' })).toBeVisible()
      }
    })
  }
})
