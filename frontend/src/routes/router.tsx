import { createBrowserRouter } from "react-router-dom";

import { AppLayout } from "@/components/layout/app-layout";
import { ArchivedStoresPage } from "@/pages/archived-stores";
import { CampaignDetailPage } from "@/pages/campaign-detail";
import { CampaignNewPage } from "@/pages/campaign-new";
import { CampaignsListPage } from "@/pages/campaigns-list";
import { ListingsPage } from "@/pages/listings";
import { ProfileDashboardPage } from "@/pages/profile-dashboard";
import { ProfileNewPage } from "@/pages/profile-new";
import { ProfileSetupPage } from "@/pages/profile-setup";
import { ReprecificarTudoPage } from "@/pages/reprecificar-tudo";
import { SimulationDetailPage } from "@/pages/simulation-detail";
import { SimulationsListPage } from "@/pages/simulations-list";
import { StoreSelectorPage } from "@/pages/store-selector";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppLayout />,
    children: [
      { index: true, element: <StoreSelectorPage /> },
      { path: "profiles/new", element: <ProfileNewPage /> },
      { path: "profiles/archived", element: <ArchivedStoresPage /> },
      { path: "profiles/:id", element: <ProfileDashboardPage /> },
      { path: "profiles/:id/setup", element: <ProfileSetupPage /> },
      { path: "profiles/:id/listings", element: <ListingsPage /> },
      {
        path: "profiles/:id/reprecificar-tudo",
        element: <ReprecificarTudoPage />,
      },
      { path: "profiles/:id/simulations", element: <SimulationsListPage /> },
      {
        path: "profiles/:id/simulations/:sim_id",
        element: <SimulationDetailPage />,
      },
      { path: "profiles/:id/campaigns", element: <CampaignsListPage /> },
      { path: "profiles/:id/campaigns/new", element: <CampaignNewPage /> },
      {
        path: "profiles/:id/campaigns/:campaign_id",
        element: <CampaignDetailPage />,
      },
    ],
  },
]);
