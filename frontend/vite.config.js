import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { Agent } from 'node:https';

export default defineConfig({ plugins: [react()], server: { proxy: {
  '/api': { target: 'https://7ctg987wi2.execute-api.ap-southeast-2.amazonaws.com', changeOrigin: true, agent: new Agent({ proxyEnv: process.env }), proxyTimeout: 25000, rewrite: path => path.replace(/^\/api/, '') }
} } });
