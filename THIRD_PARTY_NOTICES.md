# Third-party notices

Extella SEO Employee builds or connects to the following upstream components. Their source code is not relicensed by this repository.

| Component | Pinned source | Copyright |
|---|---|---|
| [Agent Zero](https://github.com/agent0ai/agent-zero) | `v2.11`, container digest recorded in `MANIFEST.yaml` | Copyright (c) 2025 Agent Zero, s.r.o |
| [CrawlSEO](https://github.com/crawlseo/crawlseo) | `8683b2740eca5059faa0949c2175a7548216bd50` | Copyright (c) 2026 crawlseo |
| [SEO Audit Skill / SEOmator](https://github.com/seo-skills/seo-audit-skill) | `bbca017b56086a2959382d8260b97021736ca18f` | Copyright (c) 2024-present SEOmator |

Each component above is distributed under the MIT License at the pinned revision. Extella-specific patches are stored in `patches/`; the build process applies them to the corresponding pinned source.

## MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The copyright notices above and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

Base container images and package dependencies remain governed by their own upstream terms and notices.
