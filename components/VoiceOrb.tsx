import { useEffect, useRef, useState } from "react"

import { api } from "../lib/api"
import { Icon } from "./Icon"

// The glowing purple and white orb, drawn with a single WebGL shader so the
// extension needs no 3D library. `level` (0–1) is the live microphone volume:
// it swells the orb, brightens it and speeds up its swirl.
const vertex = `
attribute vec2 position;
varying vec2 vUv;
void main() {
  vUv = position * 0.5 + 0.5;
  gl_Position = vec4(position, 0.0, 1.0);
}
`
const fragment = `
precision highp float;
uniform float iTime;
uniform vec2 iResolution;
uniform float rot;
uniform float level;
varying vec2 vUv;

vec3 hash33(vec3 p3) {
  p3 = fract(p3 * vec3(0.1031, 0.11369, 0.13787));
  p3 += dot(p3, p3.yxz + 19.19);
  return -1.0 + 2.0 * fract(vec3(p3.x + p3.y, p3.x + p3.z, p3.y + p3.z) * p3.zyx);
}
float snoise3(vec3 p) {
  const float K1 = 0.333333333;
  const float K2 = 0.166666667;
  vec3 i = floor(p + (p.x + p.y + p.z) * K1);
  vec3 d0 = p - (i - (i.x + i.y + i.z) * K2);
  vec3 e = step(vec3(0.0), d0 - d0.yzx);
  vec3 i1 = e * (1.0 - e.zxy);
  vec3 i2 = 1.0 - e.zxy * (1.0 - e);
  vec3 d1 = d0 - (i1 - K2);
  vec3 d2 = d0 - (i2 - K1);
  vec3 d3 = d0 - 0.5;
  vec4 h = max(0.6 - vec4(dot(d0, d0), dot(d1, d1), dot(d2, d2), dot(d3, d3)), 0.0);
  vec4 n = h * h * h * h * vec4(
    dot(d0, hash33(i)),
    dot(d1, hash33(i + i1)),
    dot(d2, hash33(i + i2)),
    dot(d3, hash33(i + 1.0))
  );
  return dot(vec4(31.316), n);
}
vec4 extractAlpha(vec3 c) {
  float a = max(max(c.r, c.g), c.b);
  return vec4(c.rgb / (a + 1e-5), a);
}
const vec3 color0 = vec3(0.702, 0.475, 1.0);
const vec3 color1 = vec3(0.545, 0.0, 1.0);
const vec3 color2 = vec3(1.0, 1.0, 1.0);
const vec3 color3 = vec3(0.0, 0.0, 0.0);
const float innerRadius = 0.1;
const float noiseScale = 0.65;
float light1(float i, float a, float d) { return i / (1.0 + d * a); }
float light2(float i, float a, float d) { return i / (1.0 + d * d * a); }
vec4 draw(vec2 uv) {
  float len = length(uv);
  float invLen = len > 0.0 ? 1.0 / len : 0.0;
  float pulse = sin(iTime * 1.5) * 0.02 + level * 0.16;
  float n0 = snoise3(vec3(uv * noiseScale, iTime * 0.5)) * 0.5 + 0.5;
  float r0 = mix(mix(innerRadius + pulse, 1.0, 0.4), mix(innerRadius + pulse, 1.0, 0.6), n0);
  float d0 = distance(uv, (r0 * invLen) * uv);
  float v0 = light1(1.0, 10.0, d0);
  v0 *= smoothstep(r0 * 1.05, r0, len);
  float cl = cos(atan(uv.y, uv.x) + iTime * 2.0) * 0.5 + 0.5;
  float a = iTime * -1.0;
  vec2 pos = vec2(cos(a), sin(a)) * r0;
  float d = distance(uv, pos);
  float v1 = light2(1.5 + level * 1.5, 5.0, d);
  v1 *= light1(1.0, 50.0, d0);
  float v2 = smoothstep(1.0, mix(innerRadius, 1.0, n0 * 0.5), len);
  float v3 = smoothstep(innerRadius, mix(innerRadius, 1.0, 0.5), len);
  vec3 col = mix(color1, color2, cl);
  col = mix(col, color0, n0);
  col = mix(color3, col, v0);
  col = (col + v1) * v2 * v3;
  return extractAlpha(clamp(col, 0.0, 1.0));
}
void main() {
  vec2 uv = (vUv * iResolution - iResolution * 0.5) / min(iResolution.x, iResolution.y) * 2.0;
  float s = sin(rot);
  float c = cos(rot);
  uv = vec2(c * uv.x - s * uv.y, s * uv.x + c * uv.y);
  vec4 col = draw(uv);
  gl_FragColor = vec4(col.rgb * col.a, col.a);
}
`

function Orb({ level }: { level: React.MutableRefObject<number> }) {
  const canvas = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const el = canvas.current
    const gl = el?.getContext("webgl", {
      alpha: true,
      premultipliedAlpha: true,
      antialias: true
    })
    if (!el || !gl) return
    const shader = (type: number, source: string) => {
      const s = gl.createShader(type)!
      gl.shaderSource(s, source)
      gl.compileShader(s)
      return s
    }
    const program = gl.createProgram()!
    gl.attachShader(program, shader(gl.VERTEX_SHADER, vertex))
    gl.attachShader(program, shader(gl.FRAGMENT_SHADER, fragment))
    gl.linkProgram(program)
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return
    gl.useProgram(program)
    const buffer = gl.createBuffer()
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer)
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([-1, -1, 3, -1, -1, 3]),
      gl.STATIC_DRAW
    )
    const position = gl.getAttribLocation(program, "position")
    gl.enableVertexAttribArray(position)
    gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0)
    const u = (name: string) => gl.getUniformLocation(program, name)
    const time = u("iTime"),
      resolution = u("iResolution"),
      rot = u("rot"),
      volume = u("level")
    const calm = matchMedia("(prefers-reduced-motion: reduce)").matches
    let frame = 0,
      last = performance.now(),
      clock = 0,
      angle = 0,
      smooth = 0
    const draw = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000)
      last = now
      // Ease towards the live volume so the orb swells rather than flickers.
      smooth += (level.current - smooth) * Math.min(1, dt * 10)
      const speed = calm ? 0.25 : 1
      clock += dt * speed * (1 + smooth * 1.5)
      angle += dt * speed * (0.3 + smooth * 1.2)
      const ratio = devicePixelRatio || 1
      const w = Math.round(el.clientWidth * ratio),
        h = Math.round(el.clientHeight * ratio)
      if (el.width !== w || el.height !== h) {
        el.width = w
        el.height = h
      }
      gl.viewport(0, 0, w, h)
      gl.clearColor(0, 0, 0, 0)
      gl.clear(gl.COLOR_BUFFER_BIT)
      gl.uniform1f(time, clock)
      gl.uniform2f(resolution, w, h)
      gl.uniform1f(rot, angle)
      gl.uniform1f(volume, smooth)
      gl.drawArrays(gl.TRIANGLES, 0, 3)
      frame = requestAnimationFrame(draw)
    }
    frame = requestAnimationFrame(draw)
    return () => {
      cancelAnimationFrame(frame)
      gl.deleteBuffer(buffer)
      gl.deleteProgram(program)
    }
  }, [])
  return <canvas ref={canvas} className="voice-orb" aria-hidden="true" />
}

type Phase = "listening" | "thinking" | "speaking"
const labels: Record<Phase, string> = {
  listening: "Listening…",
  thinking: "Thinking…",
  speaking: "Speaking…"
}
// How long a pause ends what the user is saying.
const PAUSE_MS = 1200

// Threadly's voice. The server voice (ElevenLabs, called by the backend so the
// key never reaches the extension) is tried first; the browser voice is the
// fallback if the server is unreachable or over its limit.
function pickVoice() {
  const voices = window.speechSynthesis?.getVoices() || []
  const lang = navigator.language.slice(0, 2)
  const local = voices.filter((v) => v.lang.startsWith(lang))
  return (
    local.find((v) => /natural|google|samantha|karen|daniel/i.test(v.name)) ||
    local[0] ||
    null
  )
}
function speakWithBrowser(text: string, onLevel: (level: number) => void) {
  return new Promise<void>((resolve) => {
    const synth = window.speechSynthesis
    if (!synth || !text) return resolve()
    synth.cancel()
    const u = new SpeechSynthesisUtterance(text)
    u.lang = navigator.language
    const voice = pickVoice()
    if (voice) u.voice = voice
    let timer: ReturnType<typeof setInterval> | undefined
    // Browser speech has no volume feed, so the orb follows a speaking rhythm
    // and swells on each word.
    u.onstart = () => {
      let t = 0
      timer = setInterval(() => {
        t += 0.05
        onLevel(0.3 + 0.25 * Math.abs(Math.sin(t * 7)))
      }, 50)
    }
    u.onboundary = () => onLevel(0.75)
    const done = () => {
      clearInterval(timer)
      onLevel(0)
      resolve()
    }
    u.onend = done
    u.onerror = done
    synth.speak(u)
  })
}

let active: { cancelled: boolean; stop: () => void } | null = null

export function stopSpeaking() {
  if (active) {
    active.cancelled = true
    active.stop()
    active = null
  }
  window.speechSynthesis?.cancel()
}

export async function speak(text: string, onLevel: (level: number) => void) {
  stopSpeaking()
  if (!text) return
  const session = { cancelled: false, stop: () => {} }
  active = session
  console.info("[voice] requesting ElevenLabs for", text.length, "characters")
  let ctx: AudioContext | undefined
  let frame = 0
  try {
    const reply = await api<{ audio: string }>("/voice/speak", {
      text
    })
    if (session.cancelled) return
    console.info("[voice] ElevenLabs audio received, base64 length:", reply.audio.length)
    const bytes = Uint8Array.from(atob(reply.audio), (c) => c.charCodeAt(0))
    ctx = new AudioContext()
    await ctx.resume()
    const buffer = await ctx.decodeAudioData(bytes.buffer)
    if (session.cancelled) return

    const source = ctx.createBufferSource()
    source.buffer = buffer
    const analyser = ctx.createAnalyser()
    analyser.fftSize = 512
    source.connect(analyser)
    analyser.connect(ctx.destination)

    const samples = new Uint8Array(analyser.fftSize)
    const tick = () => {
      analyser.getByteTimeDomainData(samples)
      let sum = 0
      for (const v of samples) sum += ((v - 128) / 128) ** 2
      onLevel(Math.min(1, Math.sqrt(sum / samples.length) * 5))
      frame = requestAnimationFrame(tick)
    }
    await new Promise<void>((resolve) => {
      session.stop = () => {
        try {
          source.stop()
        } catch {}
        resolve()
      }
      source.onended = () => resolve()
      source.start()
      frame = requestAnimationFrame(tick)
    })
  } catch (e: any) {
    console.warn("[voice] failed", {
      status: e?.status,
      code: e?.code,
      message: e?.message
    })
    if (!session.cancelled) await speakWithBrowser(text, onLevel)
  } finally {
    cancelAnimationFrame(frame)
    onLevel(0)
    void ctx?.close()
    if (active === session) active = null
  }
}

// Voice mode: a spoken back-and-forth with Threadly. The orb listens, thinks
// and answers aloud, then listens again, until the user closes it. Requests
// and replies also land in the chat, where drafts and approvals are handled.
export type VoiceReply = string | { text: string; showChat: boolean }

export function VoiceOrb({
  respond,
  onClose
}: {
  respond: (said: string) => Promise<VoiceReply>
  onClose: (error?: string) => void
}) {
  const level = useRef(0)
  const [phase, setPhase] = useState<Phase>("listening"),
    [notice, setNotice] = useState(""),
    // Structured replies reveal selectable cards when requested. Legacy text
    // replies retain their original behavior of docking after speech.
    [docked, setDocked] = useState(false)
  const overlay = useRef<HTMLDivElement>(null)
  const phaseRef = useRef<Phase>("listening")
  const closed = useRef(false)
  const stop = useRef<() => void>(() => {})
  const move = (next: Phase) => {
    phaseRef.current = next
    setPhase(next)
  }
  const finish = (error?: string) => {
    if (closed.current) return
    closed.current = true
    stop.current()
    onClose(error)
  }
  // Leave room for the docked orb above the conversation.
  useEffect(() => {
    const root = overlay.current?.closest(".threadly")
    if (!docked || !root) return
    root.classList.add("voice-docked")
    return () => root.classList.remove("voice-docked")
  }, [docked])
  useEffect(() => {
    const Recognition =
      (window as any).SpeechRecognition ||
      (window as any).webkitSpeechRecognition
    if (!Recognition) {
      finish(
        "Voice isn’t available in this browser. You can type your request."
      )
      return
    }
    // Each effect setup owns its resources, including StrictMode's restart.
    closed.current = false
    let stream: MediaStream | null = null,
      audio: AudioContext | null = null,
      frame = 0,
      micFrame = 0,
      silence: ReturnType<typeof setTimeout> | undefined,
      heard = "",
      broken = false,
      stopped = false
    const mic = { level: 0 },
      voice = { level: 0 }
    const r = new Recognition()
    r.lang = navigator.language
    r.interimResults = true
    r.continuous = true
    const listen = () => {
      if (stopped || closed.current || broken) return
      heard = ""
      move("listening")
      try {
        r.start()
      } catch {}
    }
    const reply = async (said: string) => {
      if (stopped || closed.current) return
      move("thinking")
      try {
        r.stop()
      } catch {}
      let answer: VoiceReply
      try {
        answer = await respond(said)
      } catch {
        answer = "Sorry, something went wrong. Try again."
      }
      if (stopped || closed.current) return
      move("speaking")
      setDocked(typeof answer === "string" ? true : answer.showChat)
      await speak(
        typeof answer === "string" ? answer : answer.text,
        (value) => (voice.level = value)
      )
      if (!stopped && !closed.current) listen()
    }
    r.onresult = (e: any) => {
      if (stopped || closed.current || phaseRef.current !== "listening") return
      let text = ""
      for (let i = 0; i < e.results.length; i++)
        text += e.results[i][0].transcript
      heard = text.replace(/\s+/g, " ").trim()
      clearTimeout(silence)
      silence = setTimeout(() => {
        if (heard && phaseRef.current === "listening") void reply(heard)
      }, PAUSE_MS)
    }
    r.onerror = (e: any) => {
      if (stopped || closed.current) return
      if (e?.error === "no-speech" || e?.error === "aborted") return
      if (
        ["not-allowed", "service-not-allowed", "audio-capture"].includes(
          e?.error
        )
      ) {
        finish(
          "Couldn’t start voice. Check microphone access or type your request."
        )
        return
      }
      // Speech service trouble: stay open and say so rather than vanish.
      broken = true
      setNotice("Voice isn’t working right now. Close and type instead.")
    }
    // Chrome ends recognition after a long pause; keep listening while it is
    // the user's turn.
    r.onend = () => {
      if (phaseRef.current === "listening") listen()
    }
    // The orb follows the user's voice while listening, Threadly's while
    // speaking, and a soft pulse while thinking.
    const animate = (now: number) => {
      if (stopped) return
      const p = phaseRef.current
      level.current =
        p === "listening"
          ? mic.level
          : p === "speaking"
            ? voice.level
            : 0.12 + 0.08 * Math.sin(now / 160)
      frame = requestAnimationFrame(animate)
    }
    frame = requestAnimationFrame(animate)
    const stopResources = () => {
      // Close and React unmount can both reach this while close() is pending.
      if (stopped) return
      stopped = true
      clearTimeout(silence)
      cancelAnimationFrame(frame)
      cancelAnimationFrame(micFrame)
      r.onresult = r.onerror = r.onend = null
      try {
        r.abort()
      } catch {}
      stopSpeaking()
      stream?.getTracks().forEach((t) => t.stop())
      stream = null
      const closing = audio
      audio = null
      if (closing && closing.state !== "closed") {
        // A browser teardown may race the state check; never leak its rejection.
        void closing.close().catch(() => {})
      }
    }
    stop.current = stopResources
    listen()
    // A read-only tap on the microphone measures how loudly the user speaks.
    // If it is refused, the orb still breathes and the conversation carries on.
    void navigator.mediaDevices
      ?.getUserMedia({ audio: true })
      .then((s) => {
        if (stopped || closed.current) {
          s.getTracks().forEach((t) => t.stop())
          return
        }
        stream = s
        audio = new AudioContext()
        const analyser = audio.createAnalyser()
        analyser.fftSize = 512
        audio.createMediaStreamSource(s).connect(analyser)
        const samples = new Uint8Array(analyser.fftSize)
        const tick = () => {
          if (stopped || closed.current) return
          analyser.getByteTimeDomainData(samples)
          let sum = 0
          for (const v of samples) sum += ((v - 128) / 128) ** 2
          mic.level = Math.min(1, Math.sqrt(sum / samples.length) * 6)
          micFrame = requestAnimationFrame(tick)
        }
        tick()
      })
      .catch(() => {})
    const key = (e: KeyboardEvent) => {
      if (!stopped && e.key === "Escape") finish()
    }
    addEventListener("keydown", key)
    return () => {
      removeEventListener("keydown", key)
      closed.current = true
      stopResources()
    }
  }, [])
  return (
    <div
      ref={overlay}
      className={`voice-overlay${docked ? " docked" : ""}`}
      role="dialog"
      aria-modal="true"
      aria-label="Voice conversation">
      <button
        className="icon-button voice-close"
        aria-label="Close voice conversation"
        autoFocus
        onClick={() => finish()}>
        <Icon name="close" />
      </button>
      <Orb level={level} />
      <p className="voice-status" role="status">
        {notice || labels[phase]}
      </p>
    </div>
  )
}
