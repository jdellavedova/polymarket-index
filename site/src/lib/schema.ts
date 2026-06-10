/**
 * Single source of truth for JSON-LD structured data (schema.org).
 * Rendered through <JsonLd data={...} /> — zero client JS, build-time only.
 */

export const SITE_URL = "https://jdellavedova.com";

export const PERSON_ID = `${SITE_URL}/about#person`;

/** Full Person entity. Inlined on /about; referenced by @id elsewhere. */
export const PERSON = {
  "@context": "https://schema.org",
  "@type": "Person",
  "@id": PERSON_ID,
  name: "Joshua Della Vedova",
  jobTitle: "Associate Professor of Finance",
  email: "mailto:jdellavedova@sandiego.edu",
  image: `${SITE_URL}/headshot.jpg`,
  url: `${SITE_URL}/about`,
  worksFor: {
    "@type": "Organization",
    name: "Knauss School of Business, University of San Diego",
    url: "https://www.sandiego.edu/business/",
  },
  alumniOf: {
    "@type": "Organization",
    name: "University of Sydney",
  },
  sameAs: [
    "https://orcid.org/0000-0003-3371-9735",
    "https://papers.ssrn.com/sol3/cf_dev/AbsByAuth.cfm?per_id=2583491",
    "https://scholar.google.com/citations?user=6KY3PLwAAAAJ&hl=en",
    "https://www.linkedin.com/in/joshua-della-vedova-b5185b76/",
    "https://github.com/jdellavedova",
  ],
};

/** Compact reference to the person, for use inside other entities. */
export const PERSON_REF = { "@id": PERSON_ID };

/** Site-wide WebSite entity (emitted from Base.astro on every page). */
export const WEBSITE = {
  "@context": "https://schema.org",
  "@type": "WebSite",
  name: "Della Vedova Prediction Market Indices",
  alternateName: "DV-PMI",
  url: SITE_URL,
  author: PERSON_REF,
};

/** BreadcrumbList helper. items = [{ name, path }] where path starts with "/". */
export function breadcrumbs(items: { name: string; path: string }[]) {
  return {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: items.map((item, i) => ({
      "@type": "ListItem",
      position: i + 1,
      name: item.name,
      item: `${SITE_URL}${item.path === "/" ? "" : item.path}`,
    })),
  };
}

const LICENSE_CC_BY_4 = "https://creativecommons.org/licenses/by/4.0/";

/** Dataset entity for an index/download. identifier is the canonical URL until a Zenodo DOI is minted (TODO). */
export function dataset(opts: {
  name: string;
  description: string;
  path: string;
  csvHref?: string;
  jsonHref?: string;
  temporalCoverage?: string;
}) {
  const distribution = [];
  if (opts.csvHref) {
    distribution.push({
      "@type": "DataDownload",
      encodingFormat: "text/csv",
      contentUrl: `${SITE_URL}${opts.csvHref}`,
    });
  }
  if (opts.jsonHref) {
    distribution.push({
      "@type": "DataDownload",
      encodingFormat: "application/json",
      contentUrl: `${SITE_URL}${opts.jsonHref}`,
    });
  }
  return {
    "@type": "Dataset",
    name: opts.name,
    description: opts.description,
    url: `${SITE_URL}${opts.path}`,
    identifier: `${SITE_URL}${opts.path}`, // TODO: replace with Zenodo DOI when minted
    license: LICENSE_CC_BY_4,
    creator: PERSON_REF,
    isAccessibleForFree: true,
    ...(opts.temporalCoverage ? { temporalCoverage: opts.temporalCoverage } : {}),
    ...(distribution.length ? { distribution } : {}),
  };
}

/** Wrap a list of schema objects in a @graph with a shared @context. */
export function graph(...entities: object[]) {
  return {
    "@context": "https://schema.org",
    "@graph": entities,
  };
}

/** ScholarlyArticle from the research-page card fields. */
export function scholarlyArticle(p: {
  title: string;
  authors: string;
  venue: string;
  summary?: string;
  href?: string;
}) {
  return {
    "@type": "ScholarlyArticle",
    headline: p.title,
    name: p.title,
    author: p.authors,
    publisher: p.venue,
    ...(p.summary ? { abstract: p.summary } : {}),
    ...(p.href && p.href.startsWith("http") ? { url: p.href } : {}),
  };
}
