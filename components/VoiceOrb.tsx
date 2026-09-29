import { useEffect, useRef, useState } from "react"

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

// Voice mode: the orb fills the panel while Threadly listens. Words are
// transcribed live; closing hands them to the chat box for the user to send.
export function VoiceOrb({
  onClose
}: {
  onClose: (transcript: string, error?: string) => void
}) {
  const level = useRef(0)
  const [heard, setHeard] = useState(""),
    [live, setLive] = useState(""),
    [status, setStatus] = useState("Listening…")
  const text = useRef("")
  const closed = useRef(false)
  const stop = useRef<() => void>(() => {})
  const finish = (error?: string) => {
    if (closed.current) return
    closed.current = true
    stop.current()
    onClose(text.current.trim(), error)
  }
  useEffect(() => {
    const Recognition =
      (window as any).SpeechRecognition ||
      (window as any).webkitSpeechRecognition
    if (!Recognition) {
      finish(
        "Dictation isn’t available in this browser. You can type your request."
      )
      return
    }
    let stream: MediaStream | null = null,
      audio: AudioContext | null = null,
      meter = 0,
      ended = false,
      broken = false
    const r = new Recognition()
    r.lang = navigator.language
    r.interimResults = true
    r.continuous = true
    r.onresult = (e: any) => {
      let final = "",
        interim = ""
      for (let i = 0; i < e.results.length; i++) {
        const piece = e.results[i][0].transcript
        if (e.results[i].isFinal) final += piece
        else interim += piece
      }
      text.current = `${final}${interim}`.replace(/\s+/g, " ")
      setHeard(final)
      setLive(interim)
    }
    r.onerror = (e: any) => {
      if (e?.error === "no-speech" || e?.error === "aborted") return
      if (
        ["not-allowed", "service-not-allowed", "audio-capture"].includes(
          e?.error
        )
      ) {
        finish(
          "Couldn’t start dictation. Check microphone access or type your request."
        )
        return
      }
      // Speech service trouble: stay open and say so rather than vanish.
      broken = true
      setStatus("Dictation isn’t working right now. Close and type instead.")
    }
    // Chrome ends recognition after a long pause; keep listening until the
    // user closes the orb.
    r.onend = () => {
      if (!ended && !broken && !closed.current)
        try {
          r.start()
        } catch {
          setStatus("Paused")
        }
    }
    stop.current = () => {
      ended = true
      cancelAnimationFrame(meter)
      try {
        r.stop()
      } catch {}
      stream?.getTracks().forEach((t) => t.stop())
      void audio?.close()
    }
    try {
      r.start()
    } catch {
      finish("Couldn’t start the microphone.")
      return
    }
    // A second, read-only tap on the microphone drives the orb's size. If it
    // is refused, the orb still breathes and dictation carries on.
    void navigator.mediaDevices
      ?.getUserMedia({ audio: true })
      .then((s) => {
        if (ended) {
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
          analyser.getByteTimeDomainData(samples)
          let sum = 0
          for (const v of samples) sum += ((v - 128) / 128) ** 2
          const rms = Math.sqrt(sum / samples.length)
          level.current = Math.min(1, rms * 6)
          meter = requestAnimationFrame(tick)
        }
        tick()
      })
      .catch(() => {})
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") finish()
    }
    addEventListener("keydown", key)
    return () => {
      removeEventListener("keydown", key)
      stop.current()
    }
  }, [])
  return (
    <div
      className="voice-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Voice input">
      <button
        className="icon-button voice-close"
        aria-label="Close voice input"
        autoFocus
        onClick={() => finish()}>
        <Icon name="close" />
      </button>
      <Orb level={level} />
      <p className="voice-status" role="status">
        {status}
      </p>
      <p className="voice-transcript">
        {heard}
        <span>{live}</span>
      </p>
    </div>
  )
}
