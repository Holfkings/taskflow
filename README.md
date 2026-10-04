# TaskFlow — SaaS Landing Page

Landing page moderna para un gestor de proyectos SaaS. HTML/CSS/JS en un solo archivo, sin dependencias de build.

## Stack
- HTML5 semántico + CSS3 (custom properties, grid, flexbox)
- Inter (Google Fonts)
- Sin frameworks, sin bundler — se despliega tal cual

## Secciones
1. Hero con mockup del dashboard en HTML puro
2. Barra de logos ("Trusted by 10,000+ teams worldwide")
3. Features (grid 3 columnas, iconos SVG inline)
4. How it works (3 pasos)
5. Pricing (Starter / Pro / Enterprise con comparación)
6. Testimonials (3 tarjetas, estrellas, avatares CSS)
7. FAQ (accordion nativo con `<details>`/`<summary>`)
8. Final CTA (email + botón)

## Desarrollo local
```bash
python -m http.server 8000
# abrir http://localhost:8000
```

## Deploy
Push a `main` dispara GitHub Actions (`.github/workflows/pages.yml`) y publica
automáticamente en GitHub Pages. Sin servidor, sin coste mensual.

## Verificar deploy
```bash
gh run list --workflow pages.yml
curl -sI https://<user>.github.io/taskflow/ | head -1
```
