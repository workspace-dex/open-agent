---
name: web-artifacts-builder
description: Create HTML/React artifacts with Tailwind, shadcn/ui for claude.ai
version: 1.0.0
author: anthropic
license: Proprietary
triggers:
  - html artifact
  - react component
  - web ui
  - tailwind
  - shadcn
conditions:
  - create html
  - build interface
  - react component
  - web artifact
---

# Web Artifacts Builder

Create rich HTML artifacts using React + Tailwind CSS.

## Quick Start

```bash
# Initialize artifact project (run once)
npx create-react-app my-artifact --templateType react
cd my-artifact
npm install

# Development
npm run dev
```

## Design Guidelines

For distinctive, NON-generic AI aesthetics:
- AVOID: excessive centered layouts, purple gradients, uniform rounded corners, Inter font
- PREFER: Bold aesthetics, unique styles, memorable designs

## Stack

- React 18 + TypeScript
- Tailwind CSS
- shadcn/ui components
- Vite / Parcel bundler

## Example Artifact

```jsx
export default function App() {
  const [count, setCount] = React.useState(0)
  return (
    <div className="min-h-screen bg-stone-900 text-white p-8">
      <h1 className="text-4xl font-bold mb-4">Counter: {count}</h1>
      <button 
        onClick={() => setCount(c => c + 1)}
        className="bg-emerald-500 hover:bg-emerald-600 px-4 py-2 rounded"
      >
        Increment
      </button>
    </div>
  )
}
```

## Bundling

```bash
# Bundle to single HTML
npx parcel build src/index.js --dist-dir dist --no-source-maps
```