// Sky Tapper - flappy-style tapper, plain canvas 2D, no libraries.
// Flap to stay aloft and thread every gap. One life, real gravity.

const canvas = document.getElementById("game");
const ctx = canvas.getContext("2d");

function resizeCanvas() {
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;
}
window.addEventListener("resize", resizeCanvas);
resizeCanvas();

// ---------- Constants ----------

const BIRD_X_RATIO = 0.28;
const BIRD_RADIUS = 16;
const GRAVITY = 1500;
const FLAP_SPEED = -480;
const PIPE_WIDTH = 74;
const PIPE_GAP = 190;
const PIPE_SPEED = 220;
const PIPE_SPACING = 320; // px between pipe pairs

// ---------- Game state ----------

let bird;
let pipes;
let score;
let gameOver;
let started;

function resetGame() {
  bird = { y: canvas.height / 2, vy: 0 };
  pipes = [];
  score = 0;
  gameOver = false;
  started = false;
  spawnPipe(canvas.width + 100);
}

function spawnPipe(x) {
  const margin = 80;
  const gapY = margin + Math.random() * Math.max(1, canvas.height - margin * 2 - PIPE_GAP);
  pipes.push({ x, gapY, passed: false });
}

resetGame();

// ---------- Input ----------

function flap() {
  if (gameOver) {
    resetGame();
    return;
  }
  started = true;
  bird.vy = FLAP_SPEED;
}

window.addEventListener("keydown", (e) => {
  if (e.code === "Space" || e.code === "ArrowUp" || e.code === "KeyW") {
    e.preventDefault();
    flap();
  }
  if (e.code === "KeyR" && gameOver) resetGame();
});
window.addEventListener("pointerdown", flap);

// ---------- Update ----------

function updateBird(dt) {
  if (!started) return;
  bird.vy += GRAVITY * dt;
  bird.y += bird.vy * dt;

  if (bird.y - BIRD_RADIUS < 0) {
    bird.y = BIRD_RADIUS;
    bird.vy = 0;
  }
  if (bird.y + BIRD_RADIUS > canvas.height) {
    bird.y = canvas.height - BIRD_RADIUS;
    gameOver = true;
  }
}

function updatePipes(dt) {
  if (!started) return;
  for (const p of pipes) {
    p.x -= PIPE_SPEED * dt;
  }
  while (pipes.length && pipes[0].x < -PIPE_WIDTH) pipes.shift();

  const last = pipes[pipes.length - 1];
  if (last && last.x < canvas.width - PIPE_SPACING) {
    spawnPipe(canvas.width + PIPE_WIDTH);
  }
}

function checkCollisions() {
  if (!started) return;
  const birdX = canvas.width * BIRD_X_RATIO;
  for (const p of pipes) {
    if (!p.passed && p.x + PIPE_WIDTH < birdX) {
      p.passed = true;
      score += 1;
    }
    const withinX = birdX + BIRD_RADIUS > p.x && birdX - BIRD_RADIUS < p.x + PIPE_WIDTH;
    if (withinX) {
      const inGap = bird.y - BIRD_RADIUS > p.gapY && bird.y + BIRD_RADIUS < p.gapY + PIPE_GAP;
      if (!inGap) gameOver = true;
    }
  }
}

// ---------- Draw ----------

function draw() {
  ctx.fillStyle = "#0e1b2b";
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  // Pipes
  ctx.fillStyle = "#4fd67a";
  for (const p of pipes) {
    ctx.fillRect(p.x, 0, PIPE_WIDTH, p.gapY);
    ctx.fillRect(p.x, p.gapY + PIPE_GAP, PIPE_WIDTH, canvas.height - (p.gapY + PIPE_GAP));
  }

  // Bird
  const birdX = canvas.width * BIRD_X_RATIO;
  ctx.fillStyle = "#ffd84a";
  ctx.beginPath();
  ctx.arc(birdX, bird.y, BIRD_RADIUS, 0, Math.PI * 2);
  ctx.fill();

  // Score
  ctx.fillStyle = "#ffffff";
  ctx.font = "24px -apple-system, system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.fillText(`${score}`, canvas.width / 2, 50);
  ctx.textAlign = "left";

  if (!started && !gameOver) {
    ctx.fillStyle = "#ffffff";
    ctx.textAlign = "center";
    ctx.font = "bold 24px -apple-system, system-ui, sans-serif";
    ctx.fillText("Tap, click or press Space to start", canvas.width / 2, canvas.height / 2);
    ctx.textAlign = "left";
  }

  if (gameOver) {
    ctx.fillStyle = "rgba(0, 0, 0, 0.55)";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#ffffff";
    ctx.textAlign = "center";
    ctx.font = "bold 32px -apple-system, system-ui, sans-serif";
    ctx.fillText(`Game Over — score: ${score}`, canvas.width / 2, canvas.height / 2 - 10);
    ctx.font = "18px -apple-system, system-ui, sans-serif";
    ctx.fillText("press R, tap or click to restart", canvas.width / 2, canvas.height / 2 + 22);
    ctx.textAlign = "left";
  }
}

// ---------- Loop ----------

let lastTime = performance.now();

function loop(now) {
  const dt = Math.min((now - lastTime) / 1000, 0.05);
  lastTime = now;

  if (!gameOver) {
    updateBird(dt);
    updatePipes(dt);
    checkCollisions();
  }

  draw();
  requestAnimationFrame(loop);
}

requestAnimationFrame(loop);
