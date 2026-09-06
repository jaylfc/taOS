declare module "plyr" {
  export default class Plyr {
    constructor(element: HTMLElement, options?: Record<string, unknown>);
    play(): Promise<void>;
    destroy(): void;
  }
}
declare module "plyr/dist/plyr.css";
