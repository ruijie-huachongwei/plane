/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useSearchParams } from "next/navigation";
import { Building2 } from "lucide-react";
// plane imports
import { API_BASE_URL } from "@plane/constants";
import type { TOAuthConfigs } from "@plane/types";
// hooks
import { useInstance } from "@/hooks/store/use-instance";

export const useExtendedOAuthConfig = (oauthActionText: string): TOAuthConfigs => {
  const searchParams = useSearchParams();
  const { config } = useInstance();
  const nextPath = searchParams.get("next_path");

  return {
    isOAuthEnabled: config?.is_cas_enabled ?? false,
    oAuthOptions: [
      {
        id: "cas",
        text: `${oauthActionText} with Ruijie 单点登录`,
        icon: <Building2 className="size-[18px]" aria-hidden="true" />,
        onClick: () => {
          const params = new URLSearchParams();
          if (nextPath) params.set("next_path", nextPath);
          const query = params.toString();
          window.location.assign(`${API_BASE_URL}/auth/cas/${query ? `?${query}` : ""}`);
        },
        enabled: config?.is_cas_enabled,
      },
    ],
  };
};
