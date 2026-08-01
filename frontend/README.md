# ChanAnalyzer frontend

React + TypeScript + Vite 研究终端。默认通过 `http://127.0.0.1:8011` 的 v2 API 工作。

```bash
npm install
npm run generate:api
npm test
npm run build
```

开发：`npm run dev`。Playwright 用例：先准备 Chromium并执行 `npm run build`，再运行 `npm run test:e2e`；CI 将构建与桌面/移动测试拆为独立步骤。设计系统组件位于 `src/components/ui`，项目令牌与统一业务页面样式位于 `src/index.css`。