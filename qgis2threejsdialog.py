# -*- coding: utf-8 -*-
"""
/***************************************************************************
 Qgis2threejsDialog
                                 A QGIS plugin
 export terrain data, map canvas image and vector data to web browser
                             -------------------
        begin                : 2013-12-21
        copyright            : (C) 2013 Minoru Akagi
        email                : akaginch@gmail.com
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""
import os

from PyQt5.QtCore import Qt, QDir, QEventLoop, QSettings, qDebug
from PyQt5.QtWidgets import QAction, QDialog, QFileDialog, QMessageBox, QMenu, QTreeWidgetItem, QTreeWidgetItemIterator, QToolButton
from PyQt5.QtGui import QIcon
from qgis.core import Qgis, QgsApplication, QgsFeature, QgsMapLayer, QgsProject, QgsUnitTypes

from .ui.qgis2threejsdialog import Ui_Qgis2threejsDialog

from .conf import debug_mode, def_vals, plugin_version
from .export import exportToThreeJS
from .exportsettings import ExportSettings
from .qgis2threejscore import ObjectTreeItem, MapTo3D
from .qgis2threejstools import getLayersInProject, logMessage
from . import propertypages as ppages
from . import qgis2threejstools as tools


class Qgis2threejsDialog(QDialog):

  def __init__(self, iface, pluginManager, exportSettings=None, lastTreeItemData=None):
    QDialog.__init__(self, iface.mainWindow())
    self.iface = iface
    self.pluginManager = pluginManager
    self._settings = exportSettings or {}
    self.lastTreeItemData = lastTreeItemData
    self.localBrowsingMode = True

    self.rb_quads = self.rb_point = None

    self.templateType = None
    self.currentItem = None
    self.currentPage = None

    # Set up the user interface from Designer.
    self.ui = ui = Ui_Qgis2threejsDialog()
    ui.setupUi(self)

    self.setWindowFlags(self.windowFlags() | Qt.WindowMinimizeButtonHint)

    # output html filename
    ui.lineEdit_OutputFilename.setText(self._settings.get("OutputFilename", ""))
    ui.lineEdit_OutputFilename.setPlaceholderText("[Temporary file]")

    # settings button
    icon = QIcon(os.path.join(tools.pluginDir(), "icons", "settings.png"))
    ui.toolButton_Settings.setIcon(icon)

    # popup menu displayed when settings button is pressed
    items = [["Load Settings...", self.loadSettings],
             ["Save Settings As...", self.saveSettings],
             [None, None],
             ["Clear Settings", self.clearSettings],
             [None, None],
             ["Plugin Settings...", self.pluginSettings]]

    self.menu = QMenu()
    self.menu_actions = []
    for text, slot in items:
      if text:
        action = QAction(text, iface.mainWindow())
        action.triggered.connect(slot)
        self.menu.addAction(action)
        self.menu_actions.append(action)
      else:
        self.menu.addSeparator()

    ui.toolButton_Settings.setMenu(self.menu)
    ui.toolButton_Settings.setPopupMode(QToolButton.InstantPopup)

    # progress bar and message label
    ui.progressBar.setVisible(False)
    ui.label_MessageIcon.setVisible(False)

    # buttons
    ui.pushButton_Run.clicked.connect(self.run)
    ui.pushButton_Close.clicked.connect(self.reject)
    ui.pushButton_Help.clicked.connect(self.help)

    # set up the template combo box
    self.initTemplateList()
    self.ui.comboBox_Template.currentIndexChanged.connect(self.currentTemplateChanged)

    # set up the properties pages
    self.pages = {}
    self.pages[ppages.PAGE_WORLD] = ppages.WorldPropertyPage(self)
    self.pages[ppages.PAGE_CONTROLS] = ppages.ControlsPropertyPage(self)
    self.pages[ppages.PAGE_DEM] = ppages.DEMPropertyPage(self)
    self.pages[ppages.PAGE_VECTOR] = ppages.VectorPropertyPage(self)
    container = ui.propertyPagesContainer
    for page in self.pages.values():
      page.hide()
      container.addWidget(page)

    # build object tree
    self.topItemPages = {ObjectTreeItem.ITEM_WORLD: ppages.PAGE_WORLD, ObjectTreeItem.ITEM_CONTROLS: ppages.PAGE_CONTROLS, ObjectTreeItem.ITEM_DEM: ppages.PAGE_DEM}
    self.initObjectTree()
    self.ui.treeWidget.currentItemChanged.connect(self.currentObjectChanged)
    self.ui.treeWidget.itemChanged.connect(self.objectItemChanged)
    self.currentTemplateChanged()   # update item visibility

    ui.toolButton_Browse.clicked.connect(self.browseClicked)

  def settings(self, clean=False):
    # save settings of current panel
    item = self.ui.treeWidget.currentItem()
    if item and self.currentPage:
      self.saveProperties(item, self.currentPage)

    # plugin version
    self._settings["PluginVersion"] = plugin_version

    # template and output html file path
    self._settings["Template"] = self.ui.comboBox_Template.currentText()
    self._settings["OutputFilename"] = self.ui.lineEdit_OutputFilename.text()

    if not clean:
      return self._settings

    # clean up settings - remove layers that don't exist in the layer registry
    registry = QgsProject.instance()
    for itemId in [ObjectTreeItem.ITEM_OPTDEM, ObjectTreeItem.ITEM_POINT, ObjectTreeItem.ITEM_LINE, ObjectTreeItem.ITEM_POLYGON]:
      parent = self._settings.get(itemId, {})
      for layerId in list(parent.keys()):
        if registry.mapLayer(layerId) is None:
          del parent[layerId]

    return self._settings

  def setSettings(self, settings):
    self._settings = settings

    # template and output html file path
    templateName = settings.get("Template")
    if templateName:
      cbox = self.ui.comboBox_Template
      index = cbox.findText(templateName)
      if index != -1:
        cbox.setCurrentIndex(index)

    filename = settings.get("OutputFilename")
    if filename:
      self.ui.lineEdit_OutputFilename.setText(filename)

    # update object tree
    self.ui.treeWidget.blockSignals(True)
    self.initObjectTree()
    self.ui.treeWidget.blockSignals(False)

    # update tree item visibility
    self.templateType = None
    self.currentTemplateChanged()

  def loadSettings(self):
    # file open dialog
    directory = QgsProject.instance().homePath()
    if not directory:
      directory = os.path.split(self.ui.lineEdit_OutputFilename.text())[0]
    if not directory:
      directory = QDir.homePath()
    filterString = "Settings files (*.qto3settings);;All files (*.*)"
    filename, _ = QFileDialog.getOpenFileName(self, "Load Export Settings", directory, filterString)
    if not filename:
      return

    # load settings from file (.qto3settings)
    import json
    with open(filename) as f:
      settings = json.load(f)

    self.setSettings(settings)

  def saveSettings(self, filename=None):
    if not filename:
      # file save dialog
      directory = QgsProject.instance().homePath()
      if not directory:
        directory = os.path.split(self.ui.lineEdit_OutputFilename.text())[0]
      if not directory:
        directory = QDir.homePath()
      filename, _ = QFileDialog.getSaveFileName(self, "Save Export Settings", directory, "Settings files (*.qto3settings)")
      if not filename:
        return

      # append .qto3settings extension if filename doesn't have
      if os.path.splitext(filename)[1].lower() != ".qto3settings":
        filename += ".qto3settings"

    # save settings to file (.qto3settings)
    import json
    with open(filename, "w", encoding="UTF-8") as f:
      json.dump(self.settings(True), f, ensure_ascii=False, indent=2, sort_keys=True)

    logMessage("Settings saved: {0}".format(filename))

  def clearSettings(self):
    if QMessageBox.question(self, "Qgis2threejs", "Are you sure to clear all export settings?", QMessageBox.Ok | QMessageBox.Cancel) == QMessageBox.Ok:
      self.setSettings({})

  def pluginSettings(self):
    from .pluginsettings import SettingsDialog
    dialog = SettingsDialog(self)
    if dialog.exec_():
      self.pluginManager.reloadPlugins()
      self.pages[ppages.PAGE_DEM].initLayerComboBox()

  def showMessageBar(self, text, level=Qgis.Info):
    # from src/gui/qgsmessagebaritem.cpp
    if level == Qgis.Critical:
      msgIcon = "/mIconCritical.png"
      bgColor = "#d65253"
    elif level == Qgis.Warning:
      msgIcon = "/mIconWarn.png"
      bgColor = "#ffc800"
    else:
      msgIcon = "/mIconInfo.png"
      bgColor = "#e7f5fe"
    stylesheet = "QLabel {{ background-color:{0}; }}".format(bgColor)

    label = self.ui.label_MessageIcon
    label.setPixmap(QgsApplication.getThemeIcon(msgIcon).pixmap(24))
    label.setStyleSheet(stylesheet)
    label.setVisible(True)

    label = self.ui.label_Status
    label.setText(text)
    label.setStyleSheet(stylesheet)

  def clearMessageBar(self):
    self.ui.label_MessageIcon.setVisible(False)
    self.ui.label_Status.setText("")
    self.ui.label_Status.setStyleSheet("QLabel { background-color: rgba(0, 0, 0, 0); }")

  def initTemplateList(self):
    cbox = self.ui.comboBox_Template
    cbox.clear()
    templateDir = QDir(tools.templateDir())
    for i, entry in enumerate(templateDir.entryList(["*.html", "*.htm"])):
      cbox.addItem(entry)

      config = tools.getTemplateConfig(entry)
      # get template type
      templateType = config.get("type", "plain")
      cbox.setItemData(i, templateType, Qt.UserRole)

      # set tool tip text
      desc = config.get("description", "")
      if desc:
        cbox.setItemData(i, desc, Qt.ToolTipRole)

    # select the template of the settings
    templatePath = self._settings.get("Template")

    # if no template setting, select the last used template
    if not templatePath:
      templatePath = QSettings().value("/Qgis2threejs/lastTemplate", def_vals.template, type=str)

    if templatePath:
      index = cbox.findText(templatePath)
      if index != -1:
        cbox.setCurrentIndex(index)
      return index
    return -1

  def initObjectTree(self):
    tree = self.ui.treeWidget
    tree.clear()

    # add vector and raster layers into tree widget
    topItems = {}
    for id, name in zip(ObjectTreeItem.topItemIds, ObjectTreeItem.topItemNames):
      item = QTreeWidgetItem(tree, [name])
      item.setData(0, Qt.UserRole, id)
      topItems[id] = item

    optDEMChecked = False
    for layer in getLayersInProject():
      parentId = ObjectTreeItem.parentIdByLayer(layer)
      if parentId is None:
        continue

      item = QTreeWidgetItem(topItems[parentId], [layer.name()])
      isVisible = self._settings.get(parentId, {}).get(layer.id(), {}).get("visible", False)
      check_state = Qt.Checked if isVisible else Qt.Unchecked
      item.setData(0, Qt.CheckStateRole, check_state)
      item.setData(0, Qt.UserRole, layer.id())
      if parentId == ObjectTreeItem.ITEM_OPTDEM and isVisible:
        optDEMChecked = True

    for id, item in topItems.items():
      if id != ObjectTreeItem.ITEM_OPTDEM or optDEMChecked:
        tree.expandItem(item)

    # disable additional DEM item which is selected as main DEM
    layerId = self._settings.get(ObjectTreeItem.ITEM_DEM, {}).get("comboBox_DEMLayer")
    if layerId:
      self.primaryDEMChanged(layerId)

  def saveProperties(self, item, page):
    properties = page.properties()
    parent = item.parent()
    if parent is None:
      # top level item
      self._settings[item.data(0, Qt.UserRole)] = properties
    else:
      # layer item
      parentId = parent.data(0, Qt.UserRole)
      if parentId not in self._settings:
        self._settings[parentId] = {}
      self._settings[parentId][item.data(0, Qt.UserRole)] = properties

  def setCurrentTreeItemByData(self, data):
    it = QTreeWidgetItemIterator(self.ui.treeWidget)
    while it.value():
      if it.value().data(0, Qt.UserRole) == data:
        self.ui.treeWidget.setCurrentItem(it.value())
        return True
      it += 1
    return False

  def currentTemplateChanged(self, index=None):
    cbox = self.ui.comboBox_Template
    templateType = cbox.itemData(cbox.currentIndex(), Qt.UserRole)
    if templateType == self.templateType:
      return

    # hide items unsupported by template
    tree = self.ui.treeWidget
    for i, id in enumerate(ObjectTreeItem.topItemIds):
      hidden = (templateType == "sphere" and id != ObjectTreeItem.ITEM_CONTROLS)
      tree.topLevelItem(i).setHidden(hidden)

    # set current tree item
    if templateType == "sphere":
      tree.setCurrentItem(tree.topLevelItem(ObjectTreeItem.topItemIndex(ObjectTreeItem.ITEM_CONTROLS)))
    elif self.lastTreeItemData is None or not self.setCurrentTreeItemByData(self.lastTreeItemData):   # restore selection
      tree.setCurrentItem(tree.topLevelItem(ObjectTreeItem.topItemIndex(ObjectTreeItem.ITEM_DEM)))   # default selection for plain is DEM

    # display messages
    self.clearMessageBar()
    if templateType != "sphere":
      # show message if crs unit is degrees
      mapSettings = self.iface.mapCanvas().mapSettings()
      if mapSettings.destinationCrs().mapUnits() in [QgsUnitTypes.DistanceDegrees]:
        self.showMessageBar("The unit of current CRS is degrees, so terrain may not appear well.", Qgis.Warning)

    self.templateType = templateType

  def currentObjectChanged(self, currentItem, previousItem):
    # save properties of previous item
    if previousItem and self.currentPage:
      self.saveProperties(previousItem, self.currentPage)

    self.currentItem = currentItem
    self.currentPage = None

    # hide text browser and all pages
    self.ui.textBrowser.hide()
    for page in self.pages.values():
      page.hide()

    parent = currentItem.parent()
    if parent is None:
      topItemIndex = currentItem.data(0, Qt.UserRole)
      pageType = self.topItemPages.get(topItemIndex, ppages.PAGE_NONE)
      page = self.pages.get(pageType, None)
      if page is None:
        self.showDescription(topItemIndex)
        return

      page.setup(self._settings.get(topItemIndex))
      page.show()

    else:
      parentId = parent.data(0, Qt.UserRole)
      layerId = currentItem.data(0, Qt.UserRole)
      layer = QgsProject.instance().mapLayer(str(layerId))
      if layer is None:
        return

      layerType = layer.type()
      if layerType == QgsMapLayer.RasterLayer:
        page = self.pages[ppages.PAGE_DEM]
        page.setup(self._settings.get(parentId, {}).get(layerId, None), layer, False)
      elif layerType == QgsMapLayer.VectorLayer:
        page = self.pages[ppages.PAGE_VECTOR]
        page.setup(self._settings.get(parentId, {}).get(layerId, None), layer)
      else:
        return

      page.show()

    self.currentPage = page

  def objectItemChanged(self, item, column):
    parent = item.parent()
    if parent is None:
      return

    # checkbox of optional layer checked/unchecked
    if item == self.currentItem:
      if self.currentPage:
        # update enablement of property widgets
        self.currentPage.itemChanged(item)
    else:
      # select changed item
      self.ui.treeWidget.setCurrentItem(item)

      # set visible property
      #visible = item.data(0, Qt.CheckStateRole) == Qt.Checked
      #parentId = parent.data(0, Qt.UserRole)
      #layerId = item.data(0, Qt.UserRole)
      #self._settings.get(parentId, {}).get(layerId, {})["visible"] = visible

  def primaryDEMChanged(self, layerId):
    tree = self.ui.treeWidget
    parent = tree.topLevelItem(ObjectTreeItem.topItemIndex(ObjectTreeItem.ITEM_OPTDEM))
    tree.blockSignals(True)
    for i in range(parent.childCount()):
      item = parent.child(i)
      isPrimary = item.data(0, Qt.UserRole) == layerId
      item.setDisabled(isPrimary)
    tree.blockSignals(False)

  def showDescription(self, topItemIndex):
    fragment = {ObjectTreeItem.ITEM_OPTDEM: "additional-dem",
                ObjectTreeItem.ITEM_POINT: "point",
                ObjectTreeItem.ITEM_LINE: "line",
                ObjectTreeItem.ITEM_POLYGON: "polygon"}.get(topItemIndex)

    url = "http://qgis2threejs.readthedocs.org/en/docs-release/ExportSettings.html"
    if fragment:
      url += "#" + fragment

    html = '<a href="{0}">Online Help</a> about this item'.format(url)
    self.ui.textBrowser.setHtml(html)
    self.ui.textBrowser.show()

  def numericFields(self, layer):
    # get attributes of a sample feature and create numeric field name list
    numeric_fields = []
    f = QgsFeature()
    layer.getFeatures().nextFeature(f)
    for field in f.fields():
      isNumeric = False
      try:
        float(f.attribute(field.name()))
        isNumeric = True
      except ValueError:
        pass
      if isNumeric:
        numeric_fields.append(field.name())
    return numeric_fields

  def mapTo3d(self):
    world = self._settings.get(ObjectTreeItem.ITEM_WORLD, {})
    bs = float(world.get("lineEdit_BaseSize", def_vals.baseSize))
    ve = float(world.get("lineEdit_zFactor", def_vals.zExaggeration))
    vs = float(world.get("lineEdit_zShift", def_vals.zShift))

    return MapTo3D(self.iface.mapCanvas().mapSettings(), bs, ve, vs)

  def progress(self, percentage=None, statusMsg=None):
    ui = self.ui
    if percentage is not None:
      ui.progressBar.setValue(percentage)
      if percentage == 100:
        ui.progressBar.setVisible(False)
        ui.label_Status.setText("")
      else:
        ui.progressBar.setVisible(True)

    if statusMsg is not None:
      ui.label_Status.setText(statusMsg)
      ui.label_Status.repaint()
    QgsApplication.processEvents(QEventLoop.ExcludeUserInputEvents)

  def run(self):
    self.endPointSelection()

    ui = self.ui
    filename = ui.lineEdit_OutputFilename.text()   # ""=Temporary file
    if filename and os.path.exists(filename):
      if QMessageBox.question(self, "Qgis2threejs", "Output file already exists. Overwrite it?", QMessageBox.Ok | QMessageBox.Cancel) != QMessageBox.Ok:
        return

    # export to web (three.js)
    export_settings = ExportSettings(self.pluginManager, self.localBrowsingMode)
    export_settings.loadSettings(self.settings())
    export_settings.setMapCanvas(self.iface.mapCanvas())

    err_msg = export_settings.checkValidity()
    if err_msg is not None:
      QMessageBox.warning(self, "Qgis2threejs", err_msg or "Invalid settings")
      return

    ui.pushButton_Run.setEnabled(False)
    ui.toolButton_Settings.setVisible(False)
    self.clearMessageBar()
    self.progress(0)

    if export_settings.exportMode == ExportSettings.PLAIN_MULTI_RES:
      # update quads and point on map canvas
      self.createRubberBands(export_settings.baseExtent, export_settings.quadtree())

    # export
    ret = exportToThreeJS(export_settings, self.progress)

    self.progress(100)
    ui.pushButton_Run.setEnabled(True)

    if not ret:
      ui.toolButton_Settings.setVisible(True)
      return

    self.clearRubberBands()

    # store last selections
    settings = QSettings()
    settings.setValue("/Qgis2threejs/lastTemplate", export_settings.templatePath)
    settings.setValue("/Qgis2threejs/lastControls", export_settings.controls)

    # open web browser
    if not tools.openHTMLFile(export_settings.htmlfilename):
      ui.toolButton_Settings.setVisible(True)
      return

    # close dialog
    QDialog.accept(self)

  def reject(self):
    # save properties of current object
    item = self.ui.treeWidget.currentItem()
    if item and self.currentPage:
      self.saveProperties(item, self.currentPage)

    self.endPointSelection()
    self.clearRubberBands()
    QDialog.reject(self)

  def help(self):
    url = "http://qgis2threejs.readthedocs.org/"

    import webbrowser
    webbrowser.open(url, new=2)    # new=2: new tab if possible

  def browseClicked(self):
    directory = os.path.split(self.ui.lineEdit_OutputFilename.text())[0]
    if not directory:
      directory = QDir.homePath()
    filename, _ = QFileDialog.getSaveFileName(self, self.tr("Output filename"), directory, "HTML file (*.html *.htm)", options=QFileDialog.DontConfirmOverwrite)
    if not filename:
      return

    # append .html extension if filename doesn't have either .html or .htm
    if filename[-5:].lower() != ".html" and filename[-4:].lower() != ".htm":
      filename += ".html"

    self.ui.lineEdit_OutputFilename.setText(filename)

  def log(self, msg):
    if debug_mode:
      qDebug(msg)

