// Neon Snake - classic grid snake, plain canvas 2D, no libraries.
// Steer with arrows/WASD or a swipe. Eat food to grow. Don't hit the wall or yourself.

const canvas = document.getElementById("game");
const ctx = canvas.getContext("2d");

// ---------- Constants ----------

const CELL = 24;
const COLS = 22;
const ROWS = 18;
const START_TICK_MS = 130;
const MIN_TICK_MS = 70;
const TICK_SPEEDUP_PER_FOOD = 3;

canvas.width = COLS * CELL;
canvas.height = ROWS * CELL;

const DIRS = {
  up: { x: 0, y: -1 },
  down: { x: 0, y: 1 },
  left: { x: -1, y: 0 },
  right: { x: 1, y: 0 },
};

// ---------- Game state ----------

let snake;
let direction;
let pendingDirection;
let food;
let score;
let tickMs;
let gameOver;

function randomFoodSpot() {
  let spot;
  do {
    spot = { x: Math.floor(Math.random() * COLS), y: Math.floor(Math.random() * ROWS) };
  } while (snake.some((s) => s.x === spot.x && s.y === spot.y));
  return spot;
}

function resetGame() {
  const startX = Math.floor(COLS / 2);
  const startY = Math.floor(ROWS / 2);
  snake = [
    { x: startX, y: startY },
    { x: startX - 1, y: startY },
    { x: startX - 2, y: startY },
  ];
  direction = DIRS.right;
  pendingDirection = DIRS.right;
  score = 0;
  tickMs = START_TICK_MS;
  gameOver = false;
  food = randomFoodSpot();
}

resetGame();

// ---------- Input ----------

function isOpposite(a, b) {
  return a.x === -b.x && a.y === -b.y;
}

function queueDirection(next) {
  if (!isOpposite(next, direction)) pendingDirection = next;
}

window.addEventListener("keydown", (e) => {
  if (e.code === "KeyR" && gameOver) {
    resetGame();
    return;
  }
  if (e.code === "ArrowUp" || e.code === "KeyW") queueDirection(DIRS.up);
  else if (e.code === "ArrowDown" || e.code === "KeyS") queueDirection(DIRS.down);
  else if (e.code === "ArrowLeft" || e.code === "KeyA") queueDirection(DIRS.left);
  else if (e.code === "ArrowRight" || e.code === "KeyD") queueDirection(DIRS.right);
});

let touchStart = null;
window.addEventListener("pointerdown", (e) => {
  if (gameOver) {
    resetGame();
    return;
  }
  touchStart = { x: e.clientX, y: e.clientY };
});
window.addEventListener("pointerup", (e) => {
  if (!touchStart) return;
  const dx = e.clientX - touchStart.x;
  const dy = e.clientY - touchStart.y;
  touchStart = null;
  if (Math.hypot(dx, dy) < 12) return; // ignore taps, only swipes steer
  if (Math.abs(dx) > Math.abs(dy)) {
    queueDirection(dx > 0 ? DIRS.right : DIRS.left);
  } else {
    queueDirection(dy > 0 ? DIRS.down : DIRS.up);
  }
});

// ---------- Update ----------

function step() {
  direction = pendingDirection;
  const head = { x: snake[0].x + direction.x, y: snake[0].y + direction.y };

  if (head.x < 0 || head.x >= COLS || head.y < 0 || head.y >= ROWS) {
    gameOver = true;
    return;
  }
  if (snake.some((s) => s.x === head.x && s.y === head.y)) {
    gameOver = true;
    return;
  }

  snake.unshift(head);

  if (head.x === food.x && head.y === food.y) {
    score += 1;
    tickMs = Math.max(MIN_TICK_MS, START_TICK_MS - score * TICK_SPEEDUP_PER_FOOD);
    food = randomFoodSpot();
  } else {
    snake.pop();
  }
}

// ---------- Draw ----------

function draw() {
  ctx.fillStyle = "#05070a";
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  // Grid
  ctx.strokeStyle = "rgba(255,255,255,0.04)";
  ctx.lineWidth = 1;
  for (let x = 0; x <= COLS; x++) {
    ctx.beginPath();
    ctx.moveTo(x * CELL, 0);
    ctx.lineTo(x * CELL, canvas.height);
    ctx.stroke();
  }
  for (let y = 0; y <= ROWS; y++) {
    ctx.beginPath();
    ctx.moveTo(0, y * CELL);
    ctx.lineTo(canvas.width, y * CELL);
    ctx.stroke();
  }

  // Food
  ctx.fillStyle = "#ff4fb0";
  ctx.beginPath();
  ctx.arc(food.x * CELL + CELL / 2, food.y * CELL + CELL / 2, CELL * 0.35, 0, Math.PI * 2);
  ctx.fill();

  // Snake
  for (let i = 0; i < snake.length; i++) {
    const s = snake[i];
    ctx.fillStyle = i === 0 ? "#7dffb3" : "#39c97a";
    ctx.fillRect(s.x * CELL + 1, s.y * CELL + 1, CELL - 2, CELL - 2);
  }

  // Score
  ctx.fillStyle = "#ffffff";
  ctx.font = "18px -apple-system, system-ui, sans-serif";
  ctx.fillText(`Score: ${score}`, 10, 22);

  if (gameOver) {
    ctx.fillStyle = "rgba(0, 0, 0, 0.6)";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#ffffff";
    ctx.textAlign = "center";
    ctx.font = "bold 26px -apple-system, system-ui, sans-serif";
    ctx.fillText(`Game Over — score: ${score}`, canvas.width / 2, canvas.height / 2 - 10);
    ctx.font = "15px -apple-system, system-ui, sans-serif";
    ctx.fillText("press R or tap to restart", canvas.width / 2, canvas.height / 2 + 18);
    ctx.textAlign = "left";
  }
}

// ---------- Loop ----------
// Snake advances on a fixed tick (not every animation frame), so its speed
// stays framerate-independent and readable regardless of tickMs.

let accumulator = 0;
let lastTime = performance.now();

function loop(now) {
  const dt = Math.min(now - lastTime, 200);
  lastTime = now;

  if (!gameOver) {
    accumulator += dt;
    while (accumulator >= tickMs) {
      step();
      accumulator -= tickMs;
      if (gameOver) {
        accumulator = 0;
        break;
      }
    }
  }

  draw();
  requestAnimationFrame(loop);
}

requestAnimationFrame(loop);
