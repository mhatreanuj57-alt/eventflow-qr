import React from 'react';
import { createRoot } from 'react-dom/client';
import '@fontsource-variable/archivo';
import '@fontsource/bebas-neue';
import './styles.css';

const root = createRoot(document.getElementById('root'));
document.title = 'EventFlow QR — Events & organizers';
import('./Platform.jsx').then(({ default: Platform }) => root.render(<Platform />));
