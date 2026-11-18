"""Abaqus/CAE GUI pickers for PRIME reaction-interface generation."""

from abaqusGui import AFXObjectKeyword, AFXPickStep, AFXProcedure, AFXGuiCommand, AFXStringKeyword, getAFXApp


class ReactionInterfacePicker(AFXProcedure):
    """Pick two or more parent elements and hand them to the kernel bridge."""

    def __init__(self, owner, interface_kind):
        AFXProcedure.__init__(self, owner)
        self.interface_kind = interface_kind
        self.cmd = AFXGuiCommand(self, "open_interface_studio", "prime_uel_plugin_kernel")
        self.solidKw = AFXObjectKeyword(self.cmd, "solid_elements", True)
        self.liquidKw = AFXObjectKeyword(self.cmd, "liquid_elements", True)
        self.kindKw = AFXStringKeyword(self.cmd, "interface_kind", True, interface_kind)

    def getFirstStep(self):
        self.solidStep = AFXPickStep(
            self,
            self.solidKw,
            "第一步，选择固相单元，点击 Done 后继续选择液相单元",
            AFXPickStep.ELEMENTS,
            AFXPickStep.MANY,
            1,
            AFXPickStep.TUPLE,
        )
        return self.solidStep

    def getNextStep(self, previousStep):
        if previousStep is not self.solidStep:
            return None
        return AFXPickStep(
            self,
            self.liquidKw,
            "第二步，选择液相单元，然后点击鼠标中键或 Done 完成",
            AFXPickStep.ELEMENTS,
            AFXPickStep.MANY,
            2,
            AFXPickStep.TUPLE,
        )


toolset = getAFXApp().getAFXMainWindow().getPluginToolset()
toolset.registerGuiMenuButton(
    buttonText="PRIME UEL Studio|选择并生成 T3 反应边",
    object=ReactionInterfacePicker(toolset, "T3"),
    kernelInitString="import prime_uel_plugin_kernel",
    version="0.8.0+public",
    author="PRIME UEL Studio",
    description="先选择固相 T3 单元，再选择液相 T3 单元，批量识别跨相反应边。",
)
toolset.registerGuiMenuButton(
    buttonText="PRIME UEL Studio|选择并生成 H8 反应面",
    object=ReactionInterfacePicker(toolset, "H8"),
    kernelInitString="import prime_uel_plugin_kernel",
    version="0.8.0+public",
    author="PRIME UEL Studio",
    description="先选择固相 H8 单元，再选择液相 H8 单元，批量识别跨相反应面。",
)
