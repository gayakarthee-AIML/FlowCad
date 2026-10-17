import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { fetchStl, type Revision } from "../services/api";

type LoadState = "empty" | "loading" | "ready" | "error";

interface Props {
  revision: Revision | null;
  busy: boolean;
}

// CAD space is Z-up (see backend layout.py); three.js is Y-up.
const Z_UP_TO_Y_UP = new THREE.Matrix4().makeRotationX(-Math.PI / 2);

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#999";
}

export function ModelViewer({ revision, busy }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const three = useRef<{
    renderer: THREE.WebGLRenderer;
    scene: THREE.Scene;
    camera: THREE.PerspectiveCamera;
    controls: OrbitControls;
    material: THREE.MeshStandardMaterial;
    edges: THREE.LineBasicMaterial;
    group: THREE.Group;
    grid: THREE.GridHelper | null;
  } | null>(null);
  const [state, setState] = useState<LoadState>("empty");
  const [error, setError] = useState("");
  const [wireframe, setWireframe] = useState(false);
  const framedFor = useRef("");

  // One-time scene setup.
  useEffect(() => {
    const el = host.current!;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    } catch {
      setState("error");
      setError("This browser could not start WebGL, so the 3D preview is unavailable. Downloads still work.");
      return;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    el.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(35, 1, 0.5, 10_000);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x3a4556, 2.4));
    const key = new THREE.DirectionalLight(0xffffff, 2.6);
    key.position.set(1, 1.6, 1.2);
    scene.add(key);
    const rim = new THREE.DirectionalLight(0xffffff, 0.9);
    rim.position.set(-1.3, 0.5, -1);
    scene.add(rim);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.screenSpacePanning = true;

    const material = new THREE.MeshStandardMaterial({ color: cssVar("--part"), metalness: 0.4, roughness: 0.42 });
    const edges = new THREE.LineBasicMaterial({ color: cssVar("--part-edge") });
    const group = new THREE.Group();
    scene.add(group);
    three.current = { renderer, scene, camera, controls, material, edges, group, grid: null };

    const resize = () => {
      const w = el.clientWidth, h = el.clientHeight;
      if (!w || !h) return;
      renderer.setSize(w, h, false);
      renderer.domElement.style.width = "100%";
      renderer.domElement.style.height = "100%";
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    resize();

    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const recolor = () => {
      material.color.set(cssVar("--part"));
      edges.color.set(cssVar("--part-edge"));
      const g = three.current?.grid;
      if (g) (g.material as THREE.LineBasicMaterial).color.set(cssVar("--grid"));
    };
    media.addEventListener("change", recolor);

    let raf = 0;
    const loop = () => {
      raf = requestAnimationFrame(loop);
      controls.update();
      renderer.render(scene, camera);
    };
    loop();

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      media.removeEventListener("change", recolor);
      controls.dispose();
      renderer.dispose();
      el.removeChild(renderer.domElement);
      three.current = null;
    };
  }, []);

  // Load the STL whenever the committed revision changes.
  const stl = revision?.files.find((f) => f.format === "stl");
  useEffect(() => {
    const t = three.current;
    if (!t) return;
    if (!stl) {
      setState("empty");
      return;
    }
    let cancelled = false;
    setState("loading");
    fetchStl(stl.url)
      .then((buf) => {
        if (cancelled) return;
        const geo = new STLLoader().parse(buf);
        geo.applyMatrix4(Z_UP_TO_Y_UP);
        geo.computeVertexNormals();
        t.group.children.forEach((c) => (c as THREE.Mesh).geometry?.dispose());
        t.group.clear();
        t.group.add(new THREE.Mesh(geo, t.material));
        t.group.add(new THREE.LineSegments(new THREE.EdgesGeometry(geo, 25), t.edges));

        const size = Math.max(...(revision?.metadata.bounding_box_mm ?? [100, 100, 100]));
        if (t.grid) t.scene.remove(t.grid);
        const step = size > 200 ? 20 : 10;
        const extent = Math.ceil((size * 2.5) / step) * step;
        t.grid = new THREE.GridHelper(extent, extent / step, cssVar("--grid"), cssVar("--grid"));
        t.grid.position.set(size / 2, -0.02, -size / 2);
        t.scene.add(t.grid);

        // Re-frame when the overall size changes; keep the user's view otherwise.
        const sizeKey = (revision?.metadata.bounding_box_mm ?? []).join("x");
        if (framedFor.current !== sizeKey) {
          frame();
          framedFor.current = sizeKey;
        }
        setState("ready");
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setError(err.message);
        setState("error");
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stl?.url]);

  useEffect(() => {
    if (three.current) three.current.material.wireframe = wireframe;
  }, [wireframe]);

  function frame() {
    const t = three.current;
    if (!t || !t.group.children.length) return;
    const box = new THREE.Box3().setFromObject(t.group);
    const centre = box.getCenter(new THREE.Vector3());
    const radius = box.getSize(new THREE.Vector3()).length() / 2;
    const fit = Math.min(1, t.camera.aspect); // narrow viewports need more distance
    const dist = (radius / Math.sin(((t.camera.fov * Math.PI) / 180) / 2)) * (1.15 / fit);
    const dir = new THREE.Vector3(0.9, 0.7, 1.15).normalize();
    t.camera.position.copy(centre).addScaledVector(dir, dist);
    t.controls.target.copy(centre);
    t.controls.update();
  }

  const bb = revision?.metadata.bounding_box_mm;

  return (
    <div className="viewer" aria-busy={busy || state === "loading"}>
      <div className="viewer-canvas" ref={host} role="img" aria-label={revision ? `3D model of bracket revision ${revision.number}` : "3D preview"} />

      {state === "empty" && !busy && (
        <div className="viewer-overlay">
          <p className="overlay-title">No model yet</p>
          <p>Describe a bracket or set the dimensions, then generate. The validated solid appears here.</p>
        </div>
      )}
      {(state === "loading" || (busy && state !== "ready")) && (
        <div className="viewer-overlay"><span className="spinner" aria-hidden="true" /> <p>Building and validating the solid…</p></div>
      )}
      {state === "error" && (
        <div className="viewer-overlay error"><p className="overlay-title">Preview unavailable</p><p>{error}</p></div>
      )}

      <div className="viewer-bar">
        {revision ? (
          <span className="viewer-tag">Rev {revision.number}{bb ? ` · ${bb.map((v) => +v.toFixed(1)).join(" × ")} mm` : ""}</span>
        ) : <span />}
        <div className="viewer-tools">
          <button type="button" className="tool" aria-pressed={wireframe} onClick={() => setWireframe((w) => !w)} disabled={state !== "ready"}>Wireframe</button>
          <button type="button" className="tool" onClick={frame} disabled={state !== "ready"}>Reset view</button>
        </div>
      </div>
      {busy && state === "ready" && <div className="viewer-busy"><span className="spinner" aria-hidden="true" /> Regenerating…</div>}
      <p className="viewer-hint">Drag to rotate · scroll to zoom · right-drag to pan</p>
    </div>
  );
}
