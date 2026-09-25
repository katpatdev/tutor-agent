import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react-swc';

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/connect': {
        target: 'http://0.0.0.0:7860',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'node',
    include: ['**/*.test.ts'],
  },
});
