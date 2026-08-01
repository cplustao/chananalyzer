import { render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { describe, expect, it, vi } from "vitest"
import { HelpPage } from "./HelpPage"
Object.defineProperty(Element.prototype,"scrollIntoView",{value:vi.fn(),writable:true})
describe("HelpPage",()=>{it("covers workflow, page logic, deployment and risk",()=>{render(<MemoryRouter initialEntries={["/help?section=chan-scanner"]}><HelpPage/></MemoryRouter>);expect(screen.getByRole("heading",{name:"帮助中心"})).toBeInTheDocument();expect(screen.getByRole("heading",{name:"缠论扫描"})).toBeInTheDocument();expect(screen.getByText("风险提示")).toBeInTheDocument();expect(screen.getByText(/AUTH_MODE=admin/)).toBeInTheDocument()})})