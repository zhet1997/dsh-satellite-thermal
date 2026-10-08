import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({ plugins: [react()], base: './', server: { proxy: { '/api': 'http://127.0.0.1:8790' } }, build: { chunkSizeWarningLimit: 1000, rollupOptions: { output: { manualChunks: { three: ['three'], graph: ['@xyflow/react'] } } } } });
