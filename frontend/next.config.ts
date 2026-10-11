import type { NextConfig } from "next";
import { PHASE_PRODUCTION_BUILD } from "next/constants";
import path from "node:path";
import fs from "node:fs";

/**
 * BORIS frontend release invariant.
 *
 * Builds only run in `.boris-builds/work/<key>`. Production systemd points
 * NEXT_DIST_DIR at `.boris-builds/current`, but on process startup we resolve
 * that symlink once to the concrete immutable artifact. Therefore an atomic
 * release switch cannot change files underneath the already-running server;
 * it keeps serving its pinned old artifact until restart, while the new server
 * starts pinned to the new artifact.
 */
export default function nextConfig(phase: string): NextConfig {
  const cwd = path.resolve(process.cwd());
  const isolatedRoot = path.resolve("/root/BORIS/frontend/.boris-builds/work");
  const insideIsolatedWork = cwd.startsWith(`${isolatedRoot}${path.sep}`);

  if (
    phase === PHASE_PRODUCTION_BUILD &&
    (process.env.BORIS_ISOLATED_BUILD !== "1" || !insideIsolatedWork)
  ) {
    throw new Error(
      "BORIS_SAFE_BUILD_REQUIRED: run `npm run build`; direct `next build` in the production tree is forbidden."
    );
  }

  let distDir = process.env.NEXT_DIST_DIR || ".next";
  if (phase !== PHASE_PRODUCTION_BUILD && distDir === ".boris-builds/current") {
    const current = path.join(cwd, distDir);
    const resolved = fs.realpathSync(current);
    const artifactRoot = path.join(cwd, ".boris-builds", "artifacts") + path.sep;
    if (!resolved.startsWith(artifactRoot)) {
      throw new Error("BORIS_RELEASE_INVALID: current must resolve inside .boris-builds/artifacts");
    }
    distDir = path.relative(cwd, resolved);
  }

  return {
    distDir,
    async headers() {
      const messengerCsp = [
        "default-src 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
        "script-src 'self' 'unsafe-inline' https://mc.yandex.ru https://challenges.cloudflare.com",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob: https://mc.yandex.ru",
        "font-src 'self' data:",
        "connect-src 'self' https://mc.yandex.ru https://challenges.cloudflare.com",
        "frame-src https://challenges.cloudflare.com",
        "worker-src 'self' blob:",
      ].join("; ");
      return [{
        source: "/messenger/:path*",
        headers: [
          { key: "Content-Security-Policy", value: messengerCsp },
          { key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(self), microphone=(self), geolocation=(), payment=()" },
        ],
      }];
    },
    async redirects() {
      return [
        {
          source: "/tg",
          destination: "/?utm_source=telegram&utm_medium=social&utm_campaign=free_test_launch&utm_content=ocean_clients",
          permanent: false,
        },
        {
          source: "/youtube",
          destination: "/?utm_source=youtube&utm_medium=video&utm_campaign=boris_demo&utm_content=youtube_video",
          permanent: false,
        },
        {
          source: "/rutube",
          destination: "/?utm_source=rutube&utm_medium=video&utm_campaign=boris_demo&utm_content=rutube_video",
          permanent: false,
        },
        {
          source: "/vk",
          destination: "/?utm_source=vk&utm_medium=social&utm_campaign=free_test_launch&utm_content=ocean_clients",
          permanent: false,
        },
      ];
    },
  };
}
