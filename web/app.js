import { PinRenderer } from "/pin_renderer.js";

const GENERIC_VISION_INSTRUCTION = "以当前上传图片为唯一视觉来源，只保留需要触摸的教学主体；删除题干、公式、数字、字母、刻度和坐标轴。";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const ui = {
  modeCards: $$(".mode-card"), file: $("#fileInput"), drop: $("#dropZone"), preview: $("#sourcePreview"),
  empty: $("#emptyUpload"), uploadLabel: $("#uploadLabel"), meta: $("#inputMeta"), context: $("[data-ai-context]"),
  inputTitle: $("#inputTitle"), inputHint: $("#inputHint"), compile: $("#compileButton"), clear: $("#clearButton"),
  loading: $("#loading"), subject: $("#subject"), learningGoal: $("#learningGoal"), lessonContent: $("#lessonContent"),
  instruction: $("#instruction"), grid: $("#grid2d"), surface3d: $("#surface3d"),
  frameIdentity: $("#frameIdentity"), frameStats: $("#frameStats"), title: $("#surfaceTitle"),
  summary: $("#surfaceSummary"), badge: $("#modeBadge"), regions: $("#regions"), warnings: $("#warnings"),
  matrix: $("#matrixText"), publish: $("#publishButton"), hardware: $("#hardwareNote"),
  lower: $("#lowerButton"), replay: $("#replayButton"), command: $("#commandInput"), commandButton: $("#commandButton"),
  toast: $("#toast"), viewButtons: $$(".view-switch button"), aiRenderButtons: $$("[data-ai-render]"),
  history: $("#historyList"), pages: $$("[data-page]"), routeButtons: $$("[data-route]"),
  startButtons: $$("[data-start-mode]"), stepButtons: $$("[data-workspace-step]"),
  panes: $$("[data-workspace-pane]"), studioActions: $("#studioActions"), actionAssist: $("#actionAssist"),
  workflowTitle: $("#workflowTitle"), heroField: $("#heroField"),
  generationProof: $("#generationProof"), generationProofTitle: $("#generationProofTitle"),
  generationProofDetail: $("#generationProofDetail"), teachingPlan: $("#teachingPlan"),
  teachingSteps: $("#teachingSteps"), loadingTitle: $("#loadingTitle"), loadingHint: $("#loadingHint"),
  generationPhases: $$('[data-generation-phase]'),
  revisionBlock: $(".revision-block"),
  threeControls: $$('[data-3d-action]'),
};

let mode = "direct";
let aiRenderMode = "scene";
let aiSupportsVision = false;
let activeRoute = "home";
let workspaceStep = "source";
let imageDataUrl = "";
let imageSource = null;
let uploadRevision = 0;
let generationRevision = 0;
let activeGenerationController = null;
let currentSurface = null;
let shownFrame = null;
let pinRenderer = null;
let heroFieldController = null;
let toastTimer = 0;
let generationTimers = [];

const routeNames = new Set(["home", "create", "history", "device"]);
const stepTitles = { source: "准备素材", intent: "设置教学意图", result: "检查触觉结果" };

function toast(message, error = false) {
  clearTimeout(toastTimer);
  ui.toast.textContent = message;
  ui.toast.className = `toast show${error ? " error" : ""}`;
  toastTimer = window.setTimeout(() => ui.toast.className = "toast", 3600);
}

async function request(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const payload = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
  if (!response.ok || payload.ok === false) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

async function fingerprintDataUrl(dataUrl) {
  if (!window.crypto?.subtle) return `local-${dataUrl.length.toString(16)}`;
  const digest = await window.crypto.subtle.digest("SHA-256", new TextEncoder().encode(dataUrl));
  return [...new Uint8Array(digest)].slice(0, 6).map((value) => value.toString(16).padStart(2, "0")).join("");
}

function nextGenerationIdentity() {
  const runRevision = ++generationRevision;
  return {
    runRevision,
    generationId: Date.now() * 1000 + (runRevision % 1000),
  };
}

function cancelActiveGeneration() {
  generationRevision += 1;
  activeGenerationController?.abort();
  activeGenerationController = null;
  stopGenerationProgress();
  ui.loading.hidden = true;
}

function clearOutputForNewSource(sourceName = "新图片", fingerprint = "") {
  const frame = { cols: 40, rows: 50, pins: Array(2000).fill(0), frame_id: 0 };
  currentSurface = null;
  drawGrid(frame);
  pinRenderer?.updateFrame(frame);
  ui.frameIdentity.textContent = `待生成 · ${sourceName}`;
  ui.frameStats.textContent = fingerprint ? `当前文件 #${fingerprint}` : "尚未生成点阵";
  ui.title.textContent = "等待根据当前图片生成";
  ui.summary.textContent = "旧点阵已经清空；点击生成后，只使用当前预览中的图片。";
  ui.badge.textContent = "PENDING";
  ui.matrix.textContent = "等待当前图片生成…";
  ui.regions.replaceChildren();
  ui.warnings.replaceChildren();
  const notice = document.createElement("div");
  notice.className = "warning";
  notice.textContent = "已切换到新图片。旧结果只保留在历史记录中，不会继续显示或覆盖本次生成。";
  ui.warnings.append(notice);
  ui.generationProof.hidden = true;
  ui.teachingPlan.hidden = true;
  ui.publish.disabled = true;
  ui.commandButton.disabled = true;
}

function resetAiContextForNewImage() {
  ui.learningGoal.value = "";
  ui.lessonContent.value = "";
  ui.instruction.value = GENERIC_VISION_INSTRUCTION;
}

function setRoute(nextRoute, updateHash = true) {
  const route = routeNames.has(nextRoute) ? nextRoute : "home";
  activeRoute = route;
  heroFieldController?.setActive(route === "home");
  ui.pages.forEach((page) => {
    const active = page.dataset.page === route;
    page.hidden = !active;
    page.classList.toggle("active", active);
  });
  ui.routeButtons.forEach((button) => {
    const active = button.dataset.route === route;
    button.classList.toggle("active", active);
    if (button.closest("nav")) {
      if (active) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    }
  });
  if (updateHash && window.location.hash !== `#${route}`) history.pushState(null, "", `#${route}`);
  if (route === "create") window.requestAnimationFrame(() => pinRenderer?.resize());
  if (updateHash) {
    window.requestAnimationFrame(() => {
      const heading = document.querySelector(`[data-page="${route}"] h1, [data-page="${route}"] h2`);
      if (heading) {
        heading.tabIndex = -1;
        heading.focus({ preventScroll: true });
      }
    });
  }
  document.title = route === "home" ? "TouchSight EDU · 触觉学习工作台" : `${route === "create" ? "创作" : route === "history" ? "历史" : "设备"} · TouchSight EDU`;
}

function setWorkspaceStep(nextStep) {
  let step = nextStep;
  if (step === "intent" && mode !== "ai") step = "source";
  if (step === "result" && (!currentSurface || currentSurface.mode === "idle")) step = "source";
  workspaceStep = step;
  ui.panes.forEach((pane) => {
    const active = pane.dataset.workspacePane === step;
    pane.hidden = !active;
    pane.classList.toggle("active", active);
  });
  ui.stepButtons.forEach((button) => {
    const active = button.dataset.workspaceStep === step;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
    if (button.dataset.workspaceStep === "intent") button.hidden = mode !== "ai";
  });
  ui.workflowTitle.textContent = stepTitles[step] || stepTitles.source;
  ui.studioActions.hidden = step === "result";
  updateAvailability();
}

function canCompile() {
  if (workspaceStep === "result") return false;
  if (mode === "ai" && workspaceStep === "source") return true;
  if (mode === "direct") return Boolean(imageDataUrl);
  if (aiRenderMode === "image" || aiRenderMode === "vision") return Boolean(imageDataUrl);
  return Boolean(imageDataUrl || ui.learningGoal.value.trim() || ui.lessonContent.value.trim());
}

function updateAvailability() {
  ui.aiRenderButtons.forEach((button) => {
    const choice = button.dataset.aiRender;
    button.disabled = (choice === "image" && !imageDataUrl)
      || (choice === "vision" && (!imageDataUrl || !aiSupportsVision));
  });
  ui.compile.disabled = !canCompile();
  if (mode === "ai" && workspaceStep === "source") {
    ui.compile.textContent = "继续设置教学意图";
    ui.actionAssist.textContent = imageDataUrl ? "已载入图片，下一步选择理解方式" : "图片可选，也可以直接根据文字生成";
  } else if (mode === "ai") {
    ui.compile.textContent = aiRenderMode === "image"
      ? "把本次图片转成教学点阵"
      : aiRenderMode === "vision" ? "AI 提取主体并生成" : "生成 AI 教学触觉图";
    ui.actionAssist.textContent = aiRenderMode === "vision"
      ? "会删除题干、公式、数字、字母、刻度和坐标轴"
      : aiRenderMode === "image" ? "忠实保留本次图片内容" : "根据教学目标重建图形";
  } else {
    ui.compile.textContent = "生成触觉点阵";
    ui.actionAssist.textContent = imageDataUrl ? "图片已就绪" : "上传图片后即可生成";
  }
}

function setAiRenderMode(nextMode) {
  if (nextMode === "vision" && imageDataUrl && aiSupportsVision) aiRenderMode = "vision";
  else if (nextMode === "image" && imageDataUrl) aiRenderMode = "image";
  else aiRenderMode = "scene";
  ui.aiRenderButtons.forEach((button) => {
    const active = button.dataset.aiRender === aiRenderMode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  if (mode === "ai") {
    ui.inputHint.textContent = aiRenderMode === "image"
      ? "点阵只使用本次新上传的图片；本地千问仅根据文字生成教学计划。"
      : aiRenderMode === "vision"
        ? "视觉模型会看图，只保留教学主体，强制排除题干、公式、坐标轴和刻度。"
        : `本地千问根据文字规划图形；${aiSupportsVision ? "也可以切换到 AI 看图提取主体。" : "当前模型不读取上传图片。"}`;
  }
  updateAvailability();
}

function setMode(nextMode) {
  mode = nextMode;
  ui.modeCards.forEach((card) => {
    const active = card.dataset.mode === mode;
    card.classList.toggle("active", active);
    card.setAttribute("aria-pressed", String(active));
  });
  const ai = mode === "ai";
  ui.inputTitle.textContent = ai ? "上传课件或题目截图" : "上传要转换的图片";
  ui.uploadLabel.textContent = ai ? "可选：上传课件或题目截图" : "选择或拖入图片";
  ui.inputHint.textContent = ai
    ? "上传后默认让视觉模型提取主体；也可以不上传，直接根据文字生成。"
    : "整张图片会按比例进入点阵，不经过旧项目的全景裁剪。";
  if (ai) setAiRenderMode(imageDataUrl ? (aiSupportsVision ? "vision" : "image") : aiRenderMode);
  if (!ai && workspaceStep === "intent") workspaceStep = "source";
  setWorkspaceStep(workspaceStep);
}

function clearImage() {
  uploadRevision += 1;
  cancelActiveGeneration();
  imageDataUrl = "";
  imageSource = null;
  ui.file.value = "";
  ui.preview.src = "";
  ui.preview.hidden = true;
  ui.empty.hidden = false;
  ui.meta.textContent = mode === "ai" ? "图片可选" : "等待图片";
  if (mode === "ai") setAiRenderMode("scene");
  clearOutputForNewSource("等待图片");
  updateAvailability();
}

function acceptFile(file) {
  if (!file || !file.type.startsWith("image/")) return toast("请选择 PNG、JPEG 或 WebP 图片。", true);
  if (file.size > 12 * 1024 * 1024) return toast("图片不能超过 12 MB。", true);
  const candidateRevision = ++uploadRevision;
  cancelActiveGeneration();
  const reader = new FileReader();
  reader.onload = async () => {
    const nextImageDataUrl = String(reader.result || "");
    const fingerprint = await fingerprintDataUrl(nextImageDataUrl);
    if (candidateRevision !== uploadRevision) return;
    imageDataUrl = nextImageDataUrl;
    imageSource = { name: file.name, size: file.size, lastModified: file.lastModified, fingerprint };
    ui.preview.src = imageDataUrl;
    ui.preview.hidden = false;
    ui.empty.hidden = true;
    ui.meta.textContent = `${file.name} · ${(file.size / 1024).toFixed(0)} KB`;
    if (mode === "ai") {
      resetAiContextForNewImage();
      setAiRenderMode(aiSupportsVision ? "vision" : "image");
    }
    clearOutputForNewSource(file.name, fingerprint);
    toast(mode === "ai"
      ? `已切换到 ${file.name}；旧教学描述已清除，当前选择“${aiSupportsVision ? "看图提取主体" : "原图直接转点阵"}”。`
      : `已切换到 ${file.name}；旧点阵已清空。`);
    updateAvailability();
  };
  reader.onerror = () => toast("图片读取失败。", true);
  reader.readAsDataURL(file);
}

function drawGrid(frame) {
  shownFrame = frame;
  const ctx = ui.grid.getContext("2d");
  const cellW = ui.grid.width / frame.cols;
  const cellH = ui.grid.height / frame.rows;
  const colors = ["#111a22", "#6e9b97", "#9dd7d0", "#d8f7f3"];
  ctx.clearRect(0, 0, ui.grid.width, ui.grid.height);
  for (let y = 0; y < frame.rows; y++) {
    for (let x = 0; x < frame.cols; x++) {
      const level = Number(frame.pins[y * frame.cols + x] || 0);
      const left = x * cellW;
      const top = y * cellH;
      ctx.fillStyle = colors[0];
      ctx.fillRect(left, top, cellW, cellH);
      ctx.strokeStyle = "rgba(255,255,255,.14)";
      ctx.strokeRect(left + .5, top + .5, cellW - 1, cellH - 1);
      if (level) {
        const radius = Math.min(cellW, cellH) * (.20 + level * .055);
        ctx.beginPath();
        ctx.arc(left + cellW / 2, top + cellH / 2, radius, 0, Math.PI * 2);
        ctx.fillStyle = colors[level];
        ctx.fill();
      }
    }
  }
}

function matrixText(frame) {
  const rows = [];
  for (let y = 0; y < frame.rows; y++) rows.push(frame.pins.slice(y * frame.cols, (y + 1) * frame.cols).join(" "));
  return rows.join("\n");
}

function speak(text) {
  if (!text || !("speechSynthesis" in window)) return;
  speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "zh-CN";
  speechSynthesis.speak(utterance);
}

function setGenerationPhase(index) {
  ui.generationPhases.forEach((phase, phaseIndex) => {
    phase.classList.toggle("active", phaseIndex === index);
    phase.classList.toggle("done", phaseIndex < index);
  });
}

function startGenerationProgress(renderMode) {
  generationTimers.forEach(window.clearTimeout);
  generationTimers = [];
  const vision = renderMode === "vision";
  const direct = renderMode === "direct";
  const revision = renderMode === "revision";
  const phaseLabels = direct
    ? ["读取图片", "提取层级", "编译点阵"]
    : revision ? ["读取指令", "调整场景", "编译点阵"]
      : ["理解输入", "规划教学", "编译点阵"];
  ui.generationPhases.forEach((phase, index) => { phase.textContent = phaseLabels[index]; });
  ui.loadingTitle.textContent = direct ? "正在计算图片点阵…"
    : revision ? "正在生成下一步教学图…"
      : vision ? "正在调用视觉模型理解图片…" : "正在调用模型规划教学图…";
  ui.loadingHint.textContent = direct ? "保持原图比例，不裁剪、不拉伸"
    : revision ? "根据教学指令重组当前触觉场景"
      : vision
    ? "识别教学主体，并排除题干、公式、数字、字母与坐标轴"
    : "根据学习目标生成可编译的触觉场景";
  setGenerationPhase(0);
  generationTimers.push(window.setTimeout(() => {
    ui.loadingTitle.textContent = direct ? "正在保留轮廓与明暗层级…"
      : revision ? "正在调整教学重点…" : "正在组织三步触摸教学路径…";
    setGenerationPhase(1);
  }, 1100));
  generationTimers.push(window.setTimeout(() => {
    ui.loadingTitle.textContent = direct ? "正在写入触觉高度矩阵…" : "正在把教学场景编译成点阵…";
    setGenerationPhase(2);
  }, 3200));
}

function stopGenerationProgress() {
  generationTimers.forEach(window.clearTimeout);
  generationTimers = [];
}

function renderGenerationProof(surface) {
  const context = surface.context || {};
  const source = context.generation_source || (surface.mode === "direct" ? "direct-compiler" : "unknown");
  const sourceLabel = context.source_name
    ? `来源：${context.source_name}${context.source_fingerprint ? ` · 文件指纹 #${context.source_fingerprint}` : ""}`
    : "";
  ui.generationProof.hidden = false;
  ui.generationProof.classList.toggle("offline", source !== "model");
  if (surface.mode === "ai-image") {
    ui.generationProofTitle.textContent = "点阵只取自当前上传图片";
    ui.generationProofDetail.textContent = `${sourceLabel || "当前图片已绑定"} · AI 仅生成教学说明，不改变图片点阵`;
  } else if (source === "model") {
    const input = context.generation_input === "image+text" ? "图片 + 教学文字" : "教学文字";
    ui.generationProofTitle.textContent = context.generation_input === "image+text"
      ? "视觉模型已调用并返回教学场景"
      : "文字模型已调用并返回教学场景";
    ui.generationProofDetail.textContent = `${context.generation_model || "已配置模型"} · 输入：${input} · 输出经确定性点阵编译器校验${sourceLabel ? ` · ${sourceLabel}` : ""}`;
  } else if (source === "offline-compiler") {
    ui.generationProofTitle.textContent = "本次未调用模型";
    ui.generationProofDetail.textContent = "使用离线函数/几何规则生成；页面不会把它标成 AI 识别结果。";
  } else if (String(surface.mode || "").startsWith("ai")) {
    ui.generationProofTitle.textContent = "历史记录未保存模型来源";
    ui.generationProofDetail.textContent = "这条旧记录创建于来源追踪功能上线前，不能据此判断当时是否调用模型。";
  } else {
    ui.generationProofTitle.textContent = "确定性图片转换";
    ui.generationProofDetail.textContent = `本次直接从图片计算点阵，不需要调用 AI 模型。${sourceLabel ? ` ${sourceLabel}` : ""}`;
  }
}

function renderTeachingPlan(surface) {
  const scene = surface.scene;
  const isTeaching = String(surface.mode || "").startsWith("ai");
  ui.teachingPlan.hidden = !isTeaching;
  ui.teachingSteps.replaceChildren();
  if (!isTeaching) return;

  const primitives = Array.isArray(scene?.primitives) ? scene.primitives : [];
  const hasQuadratic = primitives.some((item) => item.type === "quadratic");
  const vision = surface.context?.generation_input === "image+text"
    && surface.context?.render_intent === "extract_subject";
  const steps = [
    {
      title: vision ? "只留下教学主体" : "建立整体触觉轮廓",
      detail: vision
        ? "效果：题干、公式、字符和坐标轴不进入点阵。实现：视觉模型重建主体，编译器再次移除坐标轴图元。"
        : `效果：先触摸当前核心结构。实现：模型规划场景后，由编译器生成 ${surface.frame.cols}×${surface.frame.rows} 点阵。`,
    },
    hasQuadratic ? {
      title: "聚焦顶点与开口方向",
      detail: "效果：顶点附近被放大，最低点和左右对称关系更清楚。实现：缩小数学视口并把顶点提升为 H3。",
      command: "放大顶点",
    } : {
      title: "按语义区域依次触摸",
      detail: "效果：学生先摸主轮廓，再确认关键转折。实现：依据模型返回的语义区域进行朗读与触摸引导。",
    },
    hasQuadratic ? {
      title: "加入第二条曲线形成对比",
      detail: "效果：H3 保留主曲线，H2 加入 y=2x²，对比开口宽度。实现：新增二次函数图元并重新编译整张点阵。",
      command: "加入 y=2x² 对比",
    } : {
      title: "复述整体空间关系",
      detail: "效果：把分散触摸的信息合成完整图形。实现：结合区域说明与整板轮廓完成回顾。",
    },
  ];

  steps.forEach((step, index) => {
    const item = document.createElement("div");
    item.className = "teaching-step";
    const copy = document.createElement("div");
    const title = document.createElement("b");
    const detail = document.createElement("small");
    title.textContent = step.title;
    detail.textContent = step.detail;
    copy.append(title, detail);
    item.append(copy);
    if (step.command && surface.mode === "ai") {
      const action = document.createElement("button");
      action.type = "button";
      action.textContent = "生成这一步";
      action.addEventListener("click", () => revise(step.command));
      item.append(action);
    } else {
      const state = document.createElement("span");
      state.className = "current-step";
      state.textContent = index === 0 ? "当前结果" : "教师引导";
      item.append(state);
    }
    ui.teachingSteps.append(item);
  });
}

function renderSurface(surface) {
  currentSurface = surface;
  const frame = surface.frame;
  drawGrid(frame);
  pinRenderer ||= new PinRenderer(ui.surface3d);
  pinRenderer.updateFrame(frame);
  ui.frameIdentity.textContent = `Frame #${frame.frame_id} · ${frame.cols}×${frame.rows}`;
  ui.frameStats.textContent = `${surface.metrics.active_pins} 个凸点 · ${(surface.metrics.active_ratio * 100).toFixed(1)}%`;
  ui.title.textContent = surface.title;
  ui.summary.textContent = surface.summary;
  ui.badge.textContent = surface.mode.toUpperCase();
  ui.matrix.textContent = matrixText(frame);
  ui.publish.disabled = surface.mode === "idle";
  ui.commandButton.disabled = surface.mode !== "ai";
  ui.revisionBlock.hidden = surface.mode !== "ai";
  renderGenerationProof(surface);
  renderTeachingPlan(surface);
  ui.warnings.innerHTML = "";
  (surface.warnings || []).forEach((warning) => {
    const item = document.createElement("div"); item.className = "warning"; item.textContent = warning; ui.warnings.append(item);
  });
  ui.regions.innerHTML = "";
  (surface.regions || []).forEach((region) => {
    const button = document.createElement("button");
    button.className = "region";
    const name = document.createElement("b");
    const description = document.createElement("small");
    name.textContent = region.name || `区域 ${region.id}`;
    description.textContent = region.description || "点击朗读";
    button.append(name, description);
    button.addEventListener("click", () => speak(region.speech || region.description || region.name));
    ui.regions.append(button);
  });
}

async function compile() {
  if (!canCompile()) return;
  if (mode === "ai" && workspaceStep === "source") {
    setWorkspaceStep("intent");
    return;
  }
  const requestedUploadRevision = uploadRevision;
  const requestedImageDataUrl = imageDataUrl;
  const requestedSource = imageSource ? { ...imageSource } : null;
  const { runRevision, generationId } = nextGenerationIdentity();
  activeGenerationController?.abort();
  const controller = new AbortController();
  activeGenerationController = controller;
  ui.loading.hidden = false;
  ui.compile.disabled = true;
  startGenerationProgress(mode === "direct" ? "direct" : aiRenderMode);
  try {
    const path = mode === "direct"
      ? "/api/direct"
      : aiRenderMode === "image" ? "/api/teaching/image" : "/api/teaching";
    const sourceBinding = {
      generation_id: generationId,
      source_name: requestedSource?.name || "",
      source_fingerprint: requestedSource?.fingerprint || "",
    };
    const body = mode === "direct" ? { image_data_url: requestedImageDataUrl, ...sourceBinding } : {
      image_data_url: requestedImageDataUrl,
      subject: ui.subject.value,
      learning_goal: ui.learningGoal.value,
      lesson_content: ui.lessonContent.value,
      instruction: ui.instruction.value,
      render_intent: aiRenderMode === "vision" ? "extract_subject" : "",
      ...sourceBinding,
    };
    const payload = await request(path, {
      method: "POST", body: JSON.stringify(body), signal: controller.signal,
    });
    if (runRevision !== generationRevision || requestedUploadRevision !== uploadRevision || payload.discarded) return;
    renderSurface(payload.surface);
    await refreshHistory();
    setWorkspaceStep("result");
    toast(
      mode === "direct"
        ? "图片已经转换成 40×50 高密度点阵。"
        : aiRenderMode === "image"
          ? "本次上传图片已经转换，教学计划由千问生成。"
          : aiRenderMode === "vision" ? "视觉模型已提取教学主体并生成点阵。" : "AI 教学触觉图已经生成。"
    );
  } catch (error) {
    if (error.name !== "AbortError" && runRevision === generationRevision) toast(error.message, true);
  } finally {
    if (runRevision === generationRevision) {
      activeGenerationController = null;
      stopGenerationProgress();
      ui.loading.hidden = true;
      updateAvailability();
    }
  }
}

async function revise(command) {
  if (!command.trim()) return;
  const { runRevision, generationId } = nextGenerationIdentity();
  activeGenerationController?.abort();
  const controller = new AbortController();
  activeGenerationController = controller;
  ui.loading.hidden = false;
  ui.commandButton.disabled = true;
  startGenerationProgress("revision");
  try {
    const payload = await request("/api/revise", {
      method: "POST",
      body: JSON.stringify({ command, generation_id: generationId }),
      signal: controller.signal,
    });
    if (runRevision !== generationRevision || payload.discarded) return;
    renderSurface(payload.surface);
    await refreshHistory();
    ui.command.value = "";
    toast("下一帧已经生成。" );
  } catch (error) {
    if (error.name !== "AbortError" && runRevision === generationRevision) toast(error.message, true);
  } finally {
    if (runRevision === generationRevision) {
      activeGenerationController = null;
      stopGenerationProgress();
      ui.loading.hidden = true;
      ui.commandButton.disabled = currentSurface?.mode !== "ai";
    }
  }
}

function drawHistoryPreview(canvas, frame) {
  const ctx = canvas.getContext("2d");
  const cellW = canvas.width / frame.cols;
  const cellH = canvas.height / frame.rows;
  const colors = ["#111a22", "#6e9b97", "#9dd7d0", "#d8f7f3"];
  ctx.fillStyle = colors[0];
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  for (let y = 0; y < frame.rows; y++) {
    for (let x = 0; x < frame.cols; x++) {
      const level = Number(frame.pins[y * frame.cols + x] || 0);
      if (!level) continue;
      ctx.fillStyle = colors[level];
      ctx.fillRect(x * cellW, y * cellH, Math.max(1, cellW), Math.max(1, cellH));
    }
  }
}

async function restoreHistory(historyId) {
  try {
    const payload = await request("/api/history/restore", {
      method: "POST", body: JSON.stringify({ history_id: historyId }),
    });
    renderSurface(payload.surface);
    setMode(String(payload.surface.mode).startsWith("ai") ? "ai" : "direct");
    setRoute("create");
    setWorkspaceStep("result");
    toast("历史点阵已经恢复，不会重新调用模型。" );
  } catch (error) {
    toast(error.message, true);
  }
}

async function refreshHistory() {
  try {
    const payload = await request("/api/history");
    ui.history.innerHTML = "";
    if (!payload.items.length) {
      const empty = document.createElement("p");
      empty.className = "history-empty";
      empty.textContent = "还没有历史记录。";
      ui.history.append(empty);
      return;
    }
    payload.items.forEach((record) => {
      const surface = record.surface;
      const button = document.createElement("button");
      button.className = "history-card";
      const canvas = document.createElement("canvas");
      canvas.width = 80;
      canvas.height = 100;
      drawHistoryPreview(canvas, surface.frame);
      const copy = document.createElement("span");
      const title = document.createElement("b");
      title.textContent = surface.title;
      const details = document.createElement("small");
      const time = new Date(record.created_at).toLocaleString("zh-CN", { hour12: false });
      details.textContent = `${time}\n${surface.frame.cols}×${surface.frame.rows} · ${String(surface.mode).toUpperCase()}`;
      details.style.whiteSpace = "pre-line";
      copy.append(title, details);
      button.append(canvas, copy);
      button.addEventListener("click", () => restoreHistory(record.history_id));
      ui.history.append(button);
    });
  } catch (error) {
    ui.history.textContent = `历史记录读取失败：${error.message}`;
  }
}

function initHeroField(canvas) {
  if (!canvas) return null;
  const context = canvas.getContext("2d");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const parent = canvas.parentElement;
  const cols = 40;
  const rows = 50;
  const heightColors = ["#17232d", "#6e9b97", "#9dd7d0", "#d8f7f3"];
  let active = false;
  let frameRequest = 0;
  let lastPaint = 0;
  let width = 0;
  let height = 0;
  let pointerX = .78;
  let pointerY = .42;
  let pointerInside = false;

  function resize() {
    const rect = canvas.getBoundingClientRect();
    const ratio = Math.min(window.devicePixelRatio || 1, 1.5);
    width = Math.max(1, rect.width);
    height = Math.max(1, rect.height);
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    draw(performance.now());
  }

  function draw(time = 0) {
    context.clearRect(0, 0, width, height);
    const mobile = width < 760;
    const fieldHeight = height * (mobile ? .5 : .78);
    const gap = Math.max(mobile ? 6.2 : 8, Math.min(22, fieldHeight / (rows - 1), width * (mobile ? .96 : .48) / (cols - 1)));
    const gridHeight = gap * (rows - 1);
    const centerX = width * (mobile ? .62 : .755) + (pointerX - .5) * (mobile ? 5 : 10);
    const centerY = height * (mobile ? .75 : .5) + (pointerY - .5) * (mobile ? 4 : 8);
    const top = centerY - gridHeight / 2;
    const seconds = reducedMotion ? 0 : time * .001;
    const scanCenter = reducedMotion ? 31 : ((seconds * 3.2) % (rows + 22)) - 11;

    const halo = context.createRadialGradient(centerX, centerY, gap * 2, centerX, centerY, gridHeight * .68);
    halo.addColorStop(0, "rgba(140,232,220,.07)");
    halo.addColorStop(.58, "rgba(72,119,126,.022)");
    halo.addColorStop(1, "rgba(7,9,16,0)");
    context.fillStyle = halo;
    context.fillRect(Math.max(0, centerX - gridHeight), 0, Math.min(width, gridHeight * 2), height);

    for (let row = 0; row < rows; row += 1) {
      const rowProgress = row / (rows - 1);
      const perspective = .9 + rowProgress * .1;
      const y = top + row * gap;
      for (let col = 0; col < cols; col += 1) {
        const x = centerX + (col - (cols - 1) / 2) * gap * perspective;
        const normalizedX = (col - (cols - 1) / 2) / ((cols - 1) / 2);
        const curveRow = 10.5 + normalizedX * normalizedX * 27 + Math.sin(seconds * 1.05 + normalizedX * 2.4) * (reducedMotion ? 0 : .28);
        const curveDistance = Math.abs(row - curveRow);
        const scanDistance = Math.abs(row - (scanCenter + col * .14));
        const pointerDistance = Math.hypot(x - pointerX * width, y - pointerY * height) / Math.max(gap, 1);
        const edgeDistance = Math.min(col, cols - 1 - col, row, rows - 1 - row);
        const edgeFade = Math.max(0, Math.min(1, edgeDistance / 5));
        let level = curveDistance < .58 ? 3 : curveDistance < 1.35 ? 2 : curveDistance < 2.15 ? 1 : 0;
        if (!reducedMotion && scanDistance < 1.25) level = Math.max(level, scanDistance < .42 ? 2 : 1);
        if (pointerInside && pointerDistance < 4.8) {
          const pointerLevel = pointerDistance < 1.65 ? 3 : pointerDistance < 3.1 ? 2 : 1;
          level = Math.max(level, pointerLevel);
        }

        const baseRadius = Math.max(1.05, gap * .13);
        const lift = level * Math.max(1.1, gap * .13);
        const tipX = x - lift * .34;
        const tipY = y - lift;

        context.globalAlpha = level ? .28 + edgeFade * .72 : .06 + edgeFade * .32;
        context.beginPath();
        context.arc(x, y, baseRadius * 1.55, 0, Math.PI * 2);
        context.fillStyle = level ? "rgba(73,104,112,.42)" : "rgba(64,79,94,.24)";
        context.fill();
        context.beginPath();
        context.arc(x, y, baseRadius, 0, Math.PI * 2);
        context.fillStyle = level ? "#20313b" : heightColors[0];
        context.fill();

        if (level > 0) {
          context.beginPath();
          context.moveTo(x, y);
          context.lineTo(tipX, tipY);
          context.strokeStyle = level === 3 ? "rgba(216,247,243,.58)" : "rgba(110,155,151,.48)";
          context.lineWidth = Math.max(1, baseRadius * .85);
          context.stroke();
          context.beginPath();
          context.arc(tipX, tipY, baseRadius * (1.02 + level * .12), 0, Math.PI * 2);
          context.fillStyle = heightColors[level];
          context.fill();
          if (level === 3) {
            context.beginPath();
            context.arc(tipX - baseRadius * .28, tipY - baseRadius * .28, Math.max(.55, baseRadius * .24), 0, Math.PI * 2);
            context.fillStyle = "rgba(255,255,255,.9)";
            context.fill();
          }
        }
        context.globalAlpha = 1;
      }
    }
  }

  function animate(time) {
    if (!active || reducedMotion || document.hidden) {
      frameRequest = 0;
      return;
    }
    if (time - lastPaint >= 32) {
      draw(time);
      lastPaint = time;
    }
    frameRequest = window.requestAnimationFrame(animate);
  }

  function startAnimation() {
    if (!frameRequest && active && !reducedMotion && !document.hidden) frameRequest = window.requestAnimationFrame(animate);
  }

  function setActive(nextActive) {
    active = Boolean(nextActive);
    window.cancelAnimationFrame(frameRequest);
    frameRequest = 0;
    draw(performance.now());
    startAnimation();
  }

  parent?.addEventListener("pointermove", (event) => {
    const rect = canvas.getBoundingClientRect();
    pointerX = (event.clientX - rect.left) / Math.max(rect.width, 1);
    pointerY = (event.clientY - rect.top) / Math.max(rect.height, 1);
    pointerInside = true;
    if (reducedMotion) draw(performance.now());
  });
  parent?.addEventListener("pointerleave", () => { pointerInside = false; });
  document.addEventListener("visibilitychange", startAnimation);
  window.addEventListener("resize", resize);
  resize();
  return { setActive };
}

ui.routeButtons.forEach((button) => button.addEventListener("click", (event) => {
  event.preventDefault();
  setRoute(button.dataset.route);
}));
ui.startButtons.forEach((button) => button.addEventListener("click", () => {
  setMode(button.dataset.startMode);
  setWorkspaceStep("source");
  setRoute("create");
}));
ui.stepButtons.forEach((button) => {
  button.addEventListener("click", () => setWorkspaceStep(button.dataset.workspaceStep));
  button.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const visible = ui.stepButtons.filter((item) => !item.hidden);
    const current = visible.indexOf(button);
    const next = event.key === "Home" ? 0 : event.key === "End" ? visible.length - 1
      : (current + (event.key === "ArrowRight" ? 1 : -1) + visible.length) % visible.length;
    visible[next].focus();
    setWorkspaceStep(visible[next].dataset.workspaceStep);
  });
});
window.addEventListener("popstate", () => setRoute(window.location.hash.slice(1), false));
ui.modeCards.forEach((card) => card.addEventListener("click", () => setMode(card.dataset.mode)));
ui.aiRenderButtons.forEach((button) => button.addEventListener("click", () => setAiRenderMode(button.dataset.aiRender)));
ui.file.addEventListener("change", () => {
  const file = ui.file.files[0];
  ui.file.value = "";
  acceptFile(file);
});
ui.clear.addEventListener("click", clearImage);
ui.compile.addEventListener("click", compile);
[ui.learningGoal, ui.lessonContent, ui.instruction].forEach((field) => field.addEventListener("input", updateAvailability));
ui.drop.addEventListener("dragover", (event) => { event.preventDefault(); ui.drop.classList.add("dragging"); });
ui.drop.addEventListener("dragleave", () => ui.drop.classList.remove("dragging"));
ui.drop.addEventListener("drop", (event) => { event.preventDefault(); ui.drop.classList.remove("dragging"); acceptFile(event.dataTransfer.files[0]); });
ui.viewButtons.forEach((button) => button.addEventListener("click", () => {
  ui.viewButtons.forEach((item) => {
    const active = item === button;
    item.classList.toggle("active", active);
    item.setAttribute("aria-pressed", String(active));
  });
  const is3d = button.dataset.view === "3d";
  ui.grid.hidden = is3d;
  ui.surface3d.hidden = !is3d;
  if (is3d) { pinRenderer?.resize(); pinRenderer?.resetView(); }
}));
ui.threeControls.forEach((button) => button.addEventListener("click", () => {
  const action = button.getAttribute("data-3d-action");
  if (action === "zoom-in") pinRenderer?.zoomBy(0.8);
  else if (action === "zoom-out") pinRenderer?.zoomBy(1.25);
  else if (action === "fit") pinRenderer?.fitToView({ preserveAngles: true });
  else if (action === "reset") pinRenderer?.resetView();
}));
ui.lower.addEventListener("click", () => {
  if (!shownFrame) return;
  drawGrid({ ...shownFrame, pins: shownFrame.pins.map(() => 0) });
  pinRenderer?.retract();
});
ui.replay.addEventListener("click", () => {
  if (currentSurface) { drawGrid(currentSurface.frame); pinRenderer?.replay(currentSurface.frame); }
});
ui.publish.addEventListener("click", async () => {
  try {
    const result = await request("/api/hardware/publish", { method: "POST", body: "{}" });
    ui.hardware.textContent = result.message;
    toast(result.message);
  } catch (error) { toast(error.message, true); }
});
ui.commandButton.addEventListener("click", () => revise(ui.command.value));
ui.command.addEventListener("keydown", (event) => { if (event.key === "Enter") revise(ui.command.value); });
$$('.command-presets button').forEach((button) => button.addEventListener("click", () => {
  ui.command.value = button.textContent;
  revise(button.textContent);
}));
ui.surface3d.addEventListener("pinselect", (event) => {
  const regionId = currentSurface?.region_map?.[event.detail.index] || 0;
  const region = currentSurface?.regions?.find((item) => Number(item.id) === Number(regionId));
  speak(region?.speech || `第 ${event.detail.y + 1} 行，第 ${event.detail.x + 1} 列，高度 H${event.detail.level}`);
});

heroFieldController = initHeroField(ui.heroField);
setMode("direct");
setWorkspaceStep("source");
setRoute(window.location.hash.slice(1) || "home", false);

try {
  const status = await request("/api/status");
  aiSupportsVision = Boolean(status.ai.vision_ready);
  const visionButton = ui.aiRenderButtons.find((button) => button.dataset.aiRender === "vision");
  if (visionButton && !aiSupportsVision) {
    visionButton.querySelector("small").textContent = "需要加载视觉模型；当前文字千问不可用";
  }
  updateAvailability();
  const physical = status.adapters.physical;
  ui.hardware.textContent = physical.configured
    ? `实体控制桥已配置：${physical.endpoint}`
    : "实体控制协议尚未冻结；目前输出同一份 JSON 和 2-bit 帧文件，不宣称真机已经刷新。";
  const initial = await request("/api/state");
  renderSurface(initial.surface);
  await refreshHistory();
} catch (error) {
  toast(error.message, true);
}
