import { mkdtemp, rm } from "node:fs/promises"
import { createServer } from "node:http"
import { tmpdir } from "node:os"
import path from "node:path"
import { chromium, expect, test } from "@playwright/test"

import { createMockBackend, target, thread, user } from "./mock-backend"

// Real built extension, synthetic Gmail selection/API and speech only.
// The test never saves a Gmail draft, sends mail or calls a model/voice provider.
for (const mode of ["artifact", "text"] as const) {
  test(`voice reveals a ${mode} draft during spoken review and keeps it usable on repeated turns`, async () => {
    const backend = createMockBackend()
    const calls: { path: string; method: string; body?: any }[] = []
    const errors: string[] = []
    const instruction = "Draft a reply to the currently open email"
    const bodyText =
      "Hi Alex,\n\nThanks for getting back to me. I appreciate your response."
    const task = {
      task_id: "voice-reply-task",
      state: "succeeded",
      version: 2,
      instruction,
      intent: "reply",
      artifact_id: "voice-reply-artifact"
    }
    let drafts = 0
    const server = createServer(async (req, res) => {
      const local = [
        "/voice/speak",
        "/assistant/conversation-turns",
        `/threads/${thread}`,
        "/assistant/artifacts/voice-reply-artifact",
        "/assistant/tasks/voice-reply-task"
      ]
      if (!local.includes(req.url!)) {
        backend.server.emit("request", req, res)
        return
      }
      let raw = ""
      for await (const chunk of req) raw += chunk
      const body = raw ? JSON.parse(raw) : undefined
      calls.push({ path: req.url!, method: req.method!, body })
      res.setHeader("Content-Type", "application/json")
      res.setHeader("Access-Control-Allow-Origin", "*")
      let data: any
      if (req.url === "/voice/speak") {
        res.statusCode = 502
        data = {
          error: {
            code: "voice_provider_error",
            message: "Synthetic voice fallback"
          }
        }
      } else if (req.url === `/threads/${thread}`) {
        data = {
          thread: {
            thread_id: thread,
            version: 1,
            subject: "Product feedback"
          },
          messages: [
            {
              gmail_msg_id: "aaa111",
              from_addr: user.email,
              is_from_user: true,
              sent_at: "2026-10-20T10:00:00Z",
              body_clean: "I am interested and have some feedback."
            },
            {
              gmail_msg_id: target,
              from_addr: "Alex <alex@example.test>",
              is_from_user: false,
              sent_at: "2026-10-21T10:00:00Z",
              body_clean:
                "Hi Tester, thanks for your interest and feedback. Best, Alex"
            }
          ]
        }
      } else if (req.url === "/assistant/tasks/voice-reply-task") data = task
      else if (req.url === "/assistant/artifacts/voice-reply-artifact") {
        data = {
          artifact_id: "voice-reply-artifact",
          task_id: task.task_id,
          revision: 1,
          is_latest: true,
          artifact: {
            kind: "draft",
            evidence: [],
            assumptions: [],
            coverage: "partial",
            content: {
              mode: "reply",
              subject: "Re: Product feedback",
              body: bodyText,
              unresolved_fields: []
            }
          },
          draft_envelope: {
            from_address: user.email,
            to: ["alex@example.test"],
            cc: [],
            bcc: [],
            reply: {
              gmail_message_id: target,
              gmail_thread_id: thread,
              subject: "Re: Product feedback"
            }
          },
          review: { state: "unreviewed", blockers: [] },
          sending_available: false
        }
      } else {
        const drafting = body.instruction === instruction
        if (drafting) drafts++
        data = {
          conversation_id: body.conversation_id,
          version: body.expected_version + 1,
          kind: drafting && mode === "artifact" ? "task" : "message",
          text: drafting ? "Here is the draft in the chat." : "You're welcome.",
          ...(drafting
            ? mode === "artifact"
              ? { task }
              : {
                  email_draft: {
                    draft_id: body.request_id,
                    recipient: "Alex",
                    subject: "Re: Product feedback",
                    body: bodyText,
                    unresolved_fields: []
                  }
                }
            : {})
        }
      }
      res.end(JSON.stringify(data))
    })
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve))
    const origin = `http://127.0.0.1:${(server.address() as any).port}`
    const profile = await mkdtemp(path.join(tmpdir(), "threadly-voice-reply-"))
    const extension = path.resolve("build/chrome-mv3-prod")
    const context = await chromium.launchPersistentContext(profile, {
      channel: "chromium",
      headless: true,
      args: [
        `--disable-extensions-except=${extension}`,
        `--load-extension=${extension}`
      ]
    })
    try {
      const worker =
        context.serviceWorkers()[0] ||
        (await context.waitForEvent("serviceworker"))
      await expect
        .poll(() =>
          worker.evaluate(() => Boolean(globalThis.chrome?.storage?.local))
        )
        .toBe(true)
      await worker.evaluate(
        async ({ origin, user }) => {
          await chrome.storage.local.set({
            backendOrigin: origin,
            threadlySession: {
              origin,
              user,
              refresh_token: "synthetic-refresh",
              jwt:
                "fixture." +
                btoa(
                  JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })
                ) +
                ".access"
            }
          })
        },
        { origin, user }
      )
      const page = await context.newPage()
      page.on("pageerror", (error) => errors.push(error.message))
      await page.setViewportSize({ width: 390, height: 850 })
      await page.addInitScript(
        ({ thread, target, email }) => {
          const state = ((window as any).voiceReply = {
            recognizer: null,
            spoken: [] as string[],
            release: () => {}
          })
          class Recognition {
            onresult: any
            onend: any
            onerror: any
            constructor() {
              state.recognizer = this
            }
            start() {}
            stop() {
              queueMicrotask(() => this.onend?.())
            }
            abort() {}
          }
          ;(window as any).SpeechRecognition = Recognition
          navigator.mediaDevices.getUserMedia = async () => {
            throw new Error("Synthetic mic disabled")
          }
          Object.defineProperty(window, "speechSynthesis", {
            value: {
              getVoices: () => [],
              cancel: () => {},
              speak: (utterance: SpeechSynthesisUtterance) => {
                state.spoken.push(utterance.text)
                const end = () =>
                  utterance.onend?.(new Event("end") as SpeechSynthesisEvent)
                if (utterance.text.startsWith("I've drafted"))
                  state.release = end
                else setTimeout(end, 10)
              }
            }
          })
          // Selection hints only; content still comes through the local API fixture.
          chrome.tabs.query = async () =>
            [
              {
                id: 999,
                url: "https://mail.google.com/mail/u/0/#inbox/FMfcgzQbfLvKxQrWnZtjbVbHkWqmLmZd"
              }
            ] as any
          chrome.tabs.sendMessage = async () =>
            ({
              threadId: thread,
              messageIds: ["aaa111", target],
              selectedMessageId: target,
              accountEmail: email,
              replyEditorOpen: false
            }) as any
        },
        { thread, target, email: user.email }
      )
      await page.goto(
        `chrome-extension://${new URL(worker.url()).host}/sidepanel.html`
      )
      const mic = page.getByRole("button", { name: "Talk to Threadly" })
      await expect(mic).toBeEnabled()
      await expect(
        page.getByText("Product feedback", { exact: true }).first()
      ).toBeVisible()
      const say = async (text: string) => {
        await expect(page.locator(".voice-status")).toHaveText("Listening…")
        await page.evaluate(
          (text) =>
            (window as any).voiceReply.recognizer.onresult({
              results: [[{ transcript: text }]]
            }),
          text
        )
      }
      await mic.click()
      await say(instruction)
      const card = page.getByLabel("Email draft", { exact: true }).last()
      await expect(card).toBeVisible()
      await expect(page.locator(".voice-overlay")).toHaveClass(/docked/)
      await expect(page.locator(".voice-status")).toHaveText("Speaking…")
      await expect(
        page.getByRole("dialog", { name: "Voice conversation" })
      ).toHaveAttribute("aria-modal", "false")
      await card
        .getByLabel("Message", { exact: true })
        .fill(bodyText + "\nEdited in voice mode.")
      await expect(card.getByLabel("To", { exact: true })).toHaveValue(
        mode === "artifact" ? "alex@example.test" : "Alex"
      )
      await page.screenshot({ path: `test-results/voice-reply-${mode}.png` })
      await page.evaluate(() => (window as any).voiceReply.release())
      await say("Thanks")
      await expect
        .poll(
          () =>
            calls.filter(
              (call) => call.path === "/assistant/conversation-turns"
            ).length
        )
        .toBe(2)
      await expect(page.locator(".voice-overlay")).toHaveClass(/docked/)
      await expect(card.getByLabel("Message", { exact: true })).toHaveValue(
        bodyText + "\nEdited in voice mode."
      )
      // Repeating the same words creates a separate turn; no stale speech/result reuse.
      await say(instruction)
      await expect.poll(() => drafts).toBe(2)
      await expect(page.getByLabel("Email draft", { exact: true })).toHaveCount(
        2
      )
      await page
        .getByRole("button", { name: "Close voice conversation" })
        .click()
      await expect(page.locator(".threadly")).not.toHaveClass(/voice-docked/)
      const turns = calls.filter(
        (call) => call.path === "/assistant/conversation-turns"
      )
      expect(turns.map((call) => call.body.instruction)).toEqual([
        instruction,
        "Thanks",
        instruction
      ])
      expect(turns.map((call) => call.body.expected_version)).toEqual([0, 1, 2])
      expect(turns[0].body.context_snapshot_id).toBe("ctx-1")
      expect(new Set(turns.map((call) => call.body.conversation_id)).size).toBe(
        1
      )
      expect(
        [...backend.calls, ...calls].filter(
          (call) =>
            call.method !== "GET" &&
            /\/gmail-drafts|\/approve|\/send|\/auth\/(logout|reconnect|disconnect)/.test(
              call.path
            )
        )
      ).toEqual([])
      expect(errors).toEqual([])
    } finally {
      await context.close()
      await new Promise<void>((resolve) => server.close(() => resolve()))
      await rm(profile, { recursive: true, force: true })
    }
  })
}
